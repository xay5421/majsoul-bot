"""雀魂自动打牌机器人 — 主入口"""
import asyncio
import logging
import random
import signal
import sys

import ms.protocol_pb2 as pb
from google.protobuf.json_format import MessageToDict

from client import MajsoulClient
from codec import decode as xor_decode
from config import load_config
from game_state import GameState
from ai.basic import BasicAI
from ai.shanten import ShantenAI
from human_like import HumanBehavior
import display
from tiles import tile_to_str, tiles_to_str, sort_tiles

logger = logging.getLogger("majsoul")

WIND = ['东', '南', '西', '北']


def _create_ai(config):
    """根据配置创建 AI 实例"""
    ai_type = config.ai.type
    if ai_type == "mortal":
        from ai.mortal import MortalAI
        mortal_dir = getattr(config.ai, 'mortal_dir', None)
        return MortalAI(mortal_dir)
    elif ai_type == "shanten":
        return ShantenAI()
    else:
        return BasicAI()


class MajsoulBot:
    """机器人主控制器"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.client = MajsoulClient()
        self.ai = _create_ai(self.config)
        ai_type = self.config.ai.type
        logger.info(f"AI 引擎: {ai_type}")
        self.human = HumanBehavior()
        self.game_state: GameState | None = None
        self.games_played = 0
        self._running = True
        self._in_game = False
        self._game_end_event = asyncio.Event()
        self._is_mortal = (ai_type == "mortal")

    def _live(self, msg: str) -> None:
        """写入对局实况日志 (game_live.log)"""
        if hasattr(self, '_live_log'):
            self._live_log.info(msg)

    async def run(self) -> None:
        """主运行循环"""
        log_level = getattr(logging, self.config.run.log_level, logging.INFO)
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        # 对局实况日志 — 写到文件，方便 tail -f 查看
        self._live_log = logging.getLogger("majsoul.live")
        self._live_log.setLevel(logging.INFO)
        self._live_log.propagate = False  # 不传播到 root logger
        live_handler = logging.FileHandler("game_live.log", mode="w", encoding="utf-8")
        live_handler.setFormatter(logging.Formatter(
            "%(asctime)s │ %(message)s", datefmt="%H:%M:%S"
        ))
        self._live_log.addHandler(live_handler)

        logger.info("🀄 雀魂机器人启动")

        try:
            await self.client.connect()

            success = await self.client.login(
                self.config.account.username,
                self.config.account.password,
            )
            if not success:
                logger.error("登录失败，退出")
                return

            # 注册事件处理
            self.client.on("game_start", self._on_game_start)
            self.client.on("action", self._on_action)
            self.client.on("game_end", self._on_game_end)
            self.client.on("game_restore", self._on_game_restore)
            await self.client.start_event_loop()

            # 启动心跳
            heartbeat_task = asyncio.create_task(self.client.heartbeat_loop())

            # 检查并重连残留对局
            reconnected = await self.client.check_and_reconnect_game()
            if reconnected:
                logger.info("已重连残留对局，等待对局结束...")
                try:
                    await asyncio.wait_for(self._game_end_event.wait(), timeout=1800)
                except asyncio.TimeoutError:
                    logger.warning("重连对局超时 (600s)")
                self.games_played += 1
                if self._running and self.config.run.max_games != 1:
                    interval = self.config.run.game_interval
                    logger.info(f"等待 {interval} 秒后继续...")
                    await asyncio.sleep(interval)

            # 主循环
            max_games = self.config.run.max_games
            match_mode = self.config.match.mode
            while self._running:
                if max_games > 0 and self.games_played >= max_games:
                    logger.info(f"已完成 {self.games_played} 局，停止匹配")
                    break

                self._game_end_event.clear()

                if match_mode == "ai":
                    room_id = await self.client.create_ai_room(
                        room_type=self.config.match.room_type,
                    )
                    if not room_id:
                        logger.error("创建房间失败，等待重试...")
                        await asyncio.sleep(10)
                        continue

                    success = await self.client.start_room()
                    if not success:
                        logger.error("开始对局失败")
                        await asyncio.sleep(10)
                        continue
                else:
                    success = await self.client.match(
                        room_type=self.config.match.room_type,
                        level=self.config.match.level,
                    )
                    if not success:
                        logger.error("匹配失败，等待重试...")
                        await asyncio.sleep(10)
                        continue

                # 等待对局结束
                logger.info("等待对局结束...")
                try:
                    await asyncio.wait_for(self._game_end_event.wait(), timeout=1800)
                except asyncio.TimeoutError:
                    logger.warning("对局超时 (600s)")

                self.games_played += 1

                if self._running and max_games != 1:
                    interval = self.config.run.game_interval
                    logger.info(f"等待 {interval} 秒后继续...")
                    await asyncio.sleep(interval)

            heartbeat_task.cancel()

        except KeyboardInterrupt:
            logger.info("用户中断")
        except Exception as e:
            logger.exception(f"运行错误: {e}")
        finally:
            await self.client.close()
            logger.info("🀄 机器人已停止")

    # ─── 事件处理 ──────────────────────────────────

    async def _on_game_start(self, seat: int, auth_res) -> None:
        """对局开始"""
        player_count = 4 if "4" in self.config.match.room_type else 3
        self.game_state = GameState(seat, player_count)
        self._in_game = True
        self.human.on_game_start()
        self.ai.on_game_start(self.game_state)
        logger.info(f"对局开始! 座位={seat}")

    async def _on_game_restore(self, game_restore) -> None:
        """断线重连 — 重放 actions 恢复游戏状态"""
        if not self.game_state:
            logger.warning("重连但没有游戏状态，无法恢复")
            return

        # 断线重连时 Mortal 状态无法同步，切换到 ShantenAI fallback
        if self._is_mortal:
            logger.warning("断线重连: Mortal 无法同步历史状态，本局使用 ShantenAI")
            self.ai._force_fallback()

        actions = game_restore.actions
        logger.info(f"🔄 重放 {len(actions)} 个动作恢复状态...")

        for action_proto in actions:
            action_name = action_proto.name
            raw_data = action_proto.data
            
            # GameRestore 里的 action data 都是 XOR 编码的（和实时推送一致）
            action_data = xor_decode(raw_data)

            try:
                # 重放时只更新状态，不做决策
                if action_name == "ActionNewRound":
                    msg = pb.ActionNewRound()
                    msg.ParseFromString(action_data)
                    d = {
                        'chang': msg.chang, 'ju': msg.ju, 'ben': msg.ben,
                        'liqibang': msg.liqibang,
                        'doras': list(msg.doras) or ([msg.dora] if msg.dora else []),
                        'scores': list(msg.scores),
                        'tiles': list(msg.tiles),
                    }
                    self.game_state.new_round(d)
                elif action_name == "ActionDealTile":
                    msg = pb.ActionDealTile()
                    msg.ParseFromString(action_data)
                    if msg.seat == self.game_state.seat and msg.tile:
                        self.game_state.on_draw(msg.seat, msg.tile)
                    elif msg.seat != self.game_state.seat:
                        self.game_state.tiles_left -= 1
                    if msg.left_tile_count:
                        self.game_state.tiles_left = msg.left_tile_count
                elif action_name == "ActionDiscardTile":
                    msg = pb.ActionDiscardTile()
                    msg.ParseFromString(action_data)
                    self.game_state.on_discard(
                        msg.seat, msg.tile, msg.moqie, msg.is_liqi
                    )
                elif action_name == "ActionChiPengGang":
                    msg = pb.ActionChiPengGang()
                    msg.ParseFromString(action_data)
                    self.game_state.on_chi_peng_gang(
                        msg.seat, msg.type, list(msg.tiles), list(msg.froms)
                    )
                elif action_name == "ActionAnGangAddGang":
                    msg = pb.ActionAnGangAddGang()
                    msg.ParseFromString(action_data)
                    if msg.type == 2:
                        tiles = [msg.tiles] if isinstance(msg.tiles, str) else list(msg.tiles)
                        self.game_state.on_ankan(msg.seat, tiles)
                    elif msg.type == 3:
                        self.game_state.on_kakan(msg.seat, msg.tiles)
            except Exception as e:
                logger.debug(f"重放 {action_name} 出错 (可忽略): {e}")

        logger.info("✅ 状态恢复完成")
        gs = self.game_state
        logger.info(
            f"恢复后手牌 ({len(gs.hand)}张): "
            f"{tiles_to_str(sort_tiles(gs.hand))}"
            f"{' | 摸牌: ' + tile_to_str(gs.draw) if gs.draw else ''}"
            f" | 牌山剩余: {gs.tiles_left}"
        )
        display.show_round_start(self.game_state)

        # 恢复后检查最后一个 action 是否需要我们响应
        if actions:
            last = actions[-1]
            last_name = last.name
            last_data = xor_decode(last.data)

            try:
                # 如果最后是 DealTile 且轮到我，需要出牌
                if last_name == "ActionDealTile":
                    msg = pb.ActionDealTile()
                    msg.ParseFromString(last_data)
                    if msg.seat == self.game_state.seat:
                        if msg.operation and msg.operation.operation_list:
                            op = MessageToDict(msg.operation, preserving_proto_field_name=True)
                            self.game_state.pending_operation = op
                            await self._process_pending_operation()
                        else:
                            await self._do_discard()
                elif last_name == "ActionDiscardTile":
                    msg = pb.ActionDiscardTile()
                    msg.ParseFromString(last_data)
                    if msg.seat != self.game_state.seat and msg.operation and msg.operation.operation_list:
                        op = MessageToDict(msg.operation, preserving_proto_field_name=True)
                        self.game_state.pending_operation = op
                        await self._process_pending_operation()
            except Exception as e:
                logger.warning(f"重连后恢复最后操作失败 (等待服务端重发): {e}")

    async def _on_action(self, action_name: str, data: bytes) -> None:
        """处理对局中的操作 (data 已经过 XOR 解密)"""
        if not self.game_state:
            logger.warning("收到操作但没有游戏状态")
            return

        try:
            if action_name == "ActionNewRound":
                await self._handle_new_round(data)
            elif action_name == "ActionDealTile":
                await self._handle_deal_tile(data)
            elif action_name == "ActionDiscardTile":
                await self._handle_discard_tile(data)
            elif action_name == "ActionChiPengGang":
                await self._handle_chi_peng_gang(data)
            elif action_name == "ActionAnGangAddGang":
                await self._handle_angang_addgang(data)
            elif action_name == "ActionHule":
                await self._handle_hule(data)
            elif action_name == "ActionNoTile":
                await self._handle_notile(data)
            elif action_name == "ActionLiuJu":
                await self._handle_liuju(data)
            else:
                logger.debug(f"未处理的操作: {action_name}")
        except Exception as e:
            logger.exception(f"处理操作 {action_name} 出错: {e}")

    async def _handle_new_round(self, data: bytes) -> None:
        """新一局 — 使用 ActionNewRound (不是 RecordNewRound)"""
        msg = pb.ActionNewRound()
        msg.ParseFromString(data)

        gs = self.game_state
        # 直接读 protobuf 属性构造 dict 传给 game_state
        d = {
            'chang': msg.chang,
            'ju': msg.ju,
            'ben': msg.ben,
            'liqibang': msg.liqibang,
            'doras': list(msg.doras) or ([msg.dora] if msg.dora else []),
            'scores': list(msg.scores),
            'tiles': list(msg.tiles),
        }

        gs.new_round(d)
        self.human.on_round_start()
        self.ai.on_round_start(gs)
        display.show_round_start(gs)

        wind = '东南西北'[gs.round_wind]
        my_wind = '东南西北'[gs.my_wind]
        hand_str = tiles_to_str(sort_tiles(gs.hand))
        draw_str = f" | 摸牌: {tile_to_str(gs.draw)}" if gs.draw else ""
        scores_str = " / ".join(f"P{i}:{p.score}" for i, p in enumerate(gs.players))
        self._live(
            f"{'='*50}\n"
            f"         │ 🀄 {wind}{gs.round_num+1}局 {gs.honba}本场  "
            f"自风={my_wind} 座位={gs.seat}\n"
            f"         │ 手牌: {hand_str}{draw_str}\n"
            f"         │ 宝牌: {tiles_to_str(gs.dora_indicators)}  "
            f"分数: {scores_str}"
        )

        # 庄家14张或有操作时需要出牌/决策
        if msg.operation and msg.operation.operation_list:
            op = MessageToDict(msg.operation, preserving_proto_field_name=True)
            gs.pending_operation = op
            await self._process_pending_operation()
        elif len(d['tiles']) == 14:
            # 庄家需要出牌
            await self._do_discard()

    async def _handle_deal_tile(self, data: bytes) -> None:
        """摸牌 — 使用 ActionDealTile"""
        msg = pb.ActionDealTile()
        msg.ParseFromString(data)

        seat = msg.seat  # 直接读 protobuf 属性，seat=0 不会丢失
        tile = msg.tile
        left = msg.left_tile_count

        gs = self.game_state

        # 通知 Mortal AI（所有玩家的摸牌都要通知）
        if self._is_mortal:
            self.ai.send_tsumo(seat, tile if seat == gs.seat else None)

        if seat != gs.seat:
            # 他家摸牌
            gs.tiles_left = left
            return

        if not tile:
            return

        gs.on_draw(seat, tile)
        gs.tiles_left = left
        display.show_draw(gs, tile)

        # 新宝牌
        for dora in msg.doras:
            if dora not in gs.dora_indicators:
                gs.on_new_dora(dora)

        # 处理操作（自摸/暗杠/加杠/立直等）
        if msg.operation and msg.operation.operation_list:
            op = MessageToDict(msg.operation, preserving_proto_field_name=True)
            gs.pending_operation = op
            await self._process_pending_operation()
        else:
            # 普通摸牌，出牌
            await self._do_discard()

    async def _handle_discard_tile(self, data: bytes) -> None:
        """出牌 — 使用 ActionDiscardTile"""
        msg = pb.ActionDiscardTile()
        msg.ParseFromString(data)

        seat = msg.seat
        tile = msg.tile
        is_draw = msg.moqie
        is_riichi = msg.is_liqi

        if seat == self.game_state.seat:
            logger.info(f"服务端确认自家出牌: {tile} (moqie={is_draw})")

        gs = self.game_state

        # 通知 Mortal AI（包括立直宣言，所有玩家的出牌都要通知）
        if self._is_mortal:
            if is_riichi:
                self.ai.send_reach(seat)
            self.ai.send_dahai(seat, tile, is_draw)
            if is_riichi:
                self.ai.send_reach_accepted(seat)

        gs.on_discard(seat, tile, is_draw, is_riichi)
        display.show_discard(gs, seat, tile, is_tsumogiri=is_draw, is_riichi=is_riichi)

        if seat == gs.seat:
            moqie = " (摸切)" if is_draw else ""
            riichi = " [立直]" if is_riichi else ""
            self._live(
                f"[巡{gs.turn:2d}] 我打: {tile_to_str(tile)}{moqie}{riichi} "
                f"| 手牌: {tiles_to_str(sort_tiles(gs.hand))}"
            )
            return  # 自己出的牌不需要响应

        # 是否有操作可以执行（吃碰杠荣和）
        if msg.operation and msg.operation.operation_list:
            op = MessageToDict(msg.operation, preserving_proto_field_name=True)
            gs.pending_operation = op
            await self._process_pending_operation()

    async def _handle_chi_peng_gang(self, data: bytes) -> None:
        """吃碰杠 — 使用 ActionChiPengGang"""
        msg = pb.ActionChiPengGang()
        msg.ParseFromString(data)

        seat = msg.seat
        type_ = msg.type
        tiles = list(msg.tiles)
        froms = list(msg.froms)

        gs = self.game_state

        # 通知 Mortal AI
        if self._is_mortal:
            # froms 里找到被吃/碰的来源
            target = froms[-1] if froms else (seat - 1) % gs.player_count
            # tiles 里最后一张是被吃/碰的牌
            pai = tiles[-1] if tiles else ""
            consumed = tiles[:-1] if tiles else []
            if type_ == 0:  # 吃
                self.ai.send_chi(seat, target, pai, consumed)
            elif type_ == 1:  # 碰
                self.ai.send_pon(seat, target, pai, consumed)
            elif type_ == 2:  # 大明杠
                self.ai.send_daiminkan(seat, target, pai, consumed)

        gs.on_chi_peng_gang(seat, type_, tiles, froms)
        type_names = {0: "吃", 1: "碰", 2: "杠"}
        call_name = type_names.get(type_, "?")
        display.show_call(gs, seat, call_name, tiles)
        who = '我' if seat == gs.seat else f'玩家{seat}'
        self._live(f"[巡{gs.turn:2d}] {who} {call_name}: {tiles_to_str(tiles)}")

        # 吃碰后可能有操作（如需要出牌）
        if msg.operation and msg.operation.operation_list:
            op = MessageToDict(msg.operation, preserving_proto_field_name=True)
            if seat == gs.seat:
                gs.pending_operation = op
                await self._process_pending_operation()
        elif seat == gs.seat:
            await self._do_discard()

    async def _handle_angang_addgang(self, data: bytes) -> None:
        """暗杠/加杠 — 使用 ActionAnGangAddGang"""
        msg = pb.ActionAnGangAddGang()
        msg.ParseFromString(data)

        seat = msg.seat
        type_ = msg.type  # 2=暗杠, 3=加杠
        tiles_str = msg.tiles

        gs = self.game_state
        if type_ == 2:
            gs.on_ankan(seat, [tiles_str] if isinstance(tiles_str, str) else list(tiles_str))
            if self._is_mortal:
                consumed = [tiles_str] * 4 if isinstance(tiles_str, str) else list(tiles_str)
                self.ai.send_ankan(seat, consumed)
        elif type_ == 3:
            gs.on_kakan(seat, tiles_str)
            if self._is_mortal:
                self.ai.send_kakan(seat, tiles_str, [])

        who = '我' if seat == gs.seat else f'玩家{seat}'
        names = {2: '暗杠', 3: '加杠'}
        logger.info(f"{who} {names.get(type_, f'杠{type_}')}: {tiles_str} (raw type={type_})")

        # 可能有抢杠和
        if msg.operation and msg.operation.operation_list:
            op = MessageToDict(msg.operation, preserving_proto_field_name=True)
            gs.pending_operation = op
            await self._process_pending_operation()

    async def _handle_hule(self, data: bytes) -> None:
        """和牌 — 使用 ActionHule"""
        msg = pb.ActionHule()
        msg.ParseFromString(data)

        gs = self.game_state
        scores = list(msg.scores) if msg.scores else None
        delta = list(msg.delta_scores) if msg.delta_scores else None

        logger.info(f"🎊 和牌! 分数变化: {delta} → {scores}")

        for hi in msg.hules:
            seat = hi.seat
            is_tsumo = hi.zimo
            who = '我' if seat == gs.seat else f'玩家{seat}'
            win_type = '自摸' if is_tsumo else '荣和'
            logger.info(f"  {who}: {win_type}")
            display.show_win(gs, seat, hi.hu_tile, is_tsumo=is_tsumo, scores=scores)
            fan_names = [f.name for f in hi.fans] if hi.fans else []
            self._live(
                f"🎊 {who}{win_type} {tile_to_str(hi.hu_tile)}"
                f"{' 役: ' + ', '.join(fan_names) if fan_names else ''}"
            )

        if scores and gs:
            parts = []
            for i in range(gs.player_count):
                old = gs.players[i].score
                new = scores[i] if i < len(scores) else old
                d_val = new - old
                me = " ←" if i == gs.seat else ""
                parts.append(f"P{i}:{old}→{new}({d_val:+d}){me}")
            self._live(f"分数: {' / '.join(parts)}")

        # 通知 Mortal 和牌/局结束
        if self._is_mortal:
            for hi in msg.hules:
                self.ai.send_hora(hi.seat,
                                  hi.seat if hi.zimo else gs.seat,  # target 简化
                                  hi.hu_tile)
            self.ai.send_end_kyoku()

        # 等一下再确认进入下一局
        delay = self.human.get_new_round_delay()
        await asyncio.sleep(delay)
        try:
            await self.client.confirm_new_round()
        except Exception as e:
            logger.debug(f"confirm_new_round: {e}")

    async def _handle_notile(self, data: bytes) -> None:
        """荒牌流局 — 使用 ActionNoTile"""
        msg = pb.ActionNoTile()
        msg.ParseFromString(data)

        logger.info("📭 荒牌流局")
        display.show_ryuukyoku(self.game_state, "荒牌流局")
        self._live("📭 荒牌流局")

        if self._is_mortal:
            self.ai.send_ryukyoku()
            self.ai.send_end_kyoku()

        delay = self.human.get_new_round_delay()
        await asyncio.sleep(delay)
        try:
            await self.client.confirm_new_round()
        except Exception as e:
            logger.debug(f"confirm_new_round: {e}")

    async def _handle_liuju(self, data: bytes) -> None:
        """中途流局 — 使用 ActionLiuJu"""
        msg = pb.ActionLiuJu()
        msg.ParseFromString(data)

        types = {1: '九种九牌', 2: '四风连打', 3: '四杠散了', 4: '四家立直'}
        reason = types.get(msg.type, f'流局({msg.type})')
        logger.info(f"🌊 {reason}")
        display.show_ryuukyoku(self.game_state, reason)
        self._live(f"🌊 {reason}")

        delay = self.human.get_new_round_delay()
        await asyncio.sleep(delay)
        try:
            await self.client.confirm_new_round()
        except Exception as e:
            logger.debug(f"confirm_new_round: {e}")

    async def _on_game_end(self, data: bytes) -> None:
        """对局结束"""
        self._in_game = False

        try:
            msg = pb.NotifyGameEndResult()
            msg.ParseFromString(data)
            d = MessageToDict(msg, preserving_proto_field_name=True)
            players = d.get('result', {}).get('players', [])
            scores = [0] * (self.game_state.player_count if self.game_state else 4)
            for p in players:
                seat = p.get('seat', 0)
                if seat < len(scores):
                    scores[seat] = p.get('total_point', 0)
            display.show_game_end(self.game_state, scores)
            # 写入实况日志
            ranked = sorted(enumerate(scores), key=lambda x: -x[1])
            lines = ["🏁 对局结束!"]
            for rank, (i, sc) in enumerate(ranked):
                me = " ← 自家" if self.game_state and i == self.game_state.seat else ""
                lines.append(f"  第{rank+1}名 P{i}: {sc}{me}")
            self._live("\n         │ ".join(lines))
        except Exception:
            logger.info("🏁 对局结束!")

        self.ai.on_game_end({})
        self._game_end_event.set()

    # ─── 决策 ──────────────────────────────────────

    async def _process_pending_operation(self) -> None:
        """处理待执行的操作"""
        gs = self.game_state
        if not gs or not gs.pending_operation:
            return

        operation = gs.pending_operation
        gs.pending_operation = None

        action = self.ai.decide_action(gs, operation)

        if action is None:
            # 跳过
            delay = self.human.get_skip_delay()
            display.show_action_decision("skip")
            logger.debug(f"跳过操作 (等待 {delay:.1f}s)")
            await asyncio.sleep(delay)
            await self.client.skip_action()

            # 如果有出牌操作(type=1)，需要自己出牌
            op_list = operation.get("operation_list", [])
            has_discard = any(op.get("type") == 1 for op in op_list)
            if has_discard:
                await self._do_discard()
            return

        action_type = action.get("type", 0)
        self.human.on_action()

        if action_type in [8, 9]:
            # 自摸/荣和
            call = "tsumo" if action_type == 8 else "ron"
            display.show_action_decision(call)
            delay = self.human.get_call_delay(call)
            await asyncio.sleep(delay)
            await self.client.win(action_type)
        elif action_type == 7:
            # 立直
            display.show_action_decision("riichi")
            delay = self.human.get_riichi_delay()
            await asyncio.sleep(delay)
            tile = action.get("tile", "")
            if not tile:
                tile = self.ai.decide_discard(gs)
            is_moqie = (tile == gs.draw)
            await self.client.discard_tile(tile, is_riichi=True, moqie=is_moqie)
        elif action_type in [2, 3, 5]:
            # 吃碰杠
            call = {2: "chi", 3: "pon", 5: "kan"}.get(action_type, "pon")
            combination = action.get("combination", [])
            display.show_action_decision(call, display.format_tiles(combination))
            delay = self.human.get_call_delay(call)
            await asyncio.sleep(delay)
            await self.client.chi_peng_gang(action_type, combination)
        elif action_type in [4, 6]:
            # 暗杠/加杠
            delay = self.human.get_call_delay("kan")
            await asyncio.sleep(delay)
            combination = action.get("combination", [])
            await self.client.chi_peng_gang(action_type, combination)
        else:
            logger.warning(f"未知操作类型: {action_type}")
            await asyncio.sleep(self.human.get_skip_delay())
            await self.client.skip_action()

    async def _do_discard(self) -> None:
        """执行出牌"""
        gs = self.game_state
        if not gs:
            return

        tile = self.ai.decide_discard(gs)
        is_moqie = (tile == gs.draw)

        # 验证出牌合法性
        full_hand = gs.get_full_hand()
        if tile not in full_hand:
            logger.warning(f"⚠️ AI 选择的牌 {tile} 不在手牌中! 手牌: {full_hand}")
            # fallback: 打摸到的牌
            if gs.draw:
                tile = gs.draw
                is_moqie = True
            elif gs.hand:
                tile = gs.hand[-1]
                is_moqie = False
            else:
                logger.error("无牌可打!")
                return

        delay = self.human.get_discard_delay(
            is_tsumogiri=is_moqie,
            hand_size=len(gs.hand),
            is_riichi=gs.players[gs.seat].riichi,
        )
        display.show_action_decision("discard", display.format_tile(tile))
        logger.info(f"出牌: {tile} ({'摸切' if is_moqie else '手切'}) [手牌: {gs.hand}, draw: {gs.draw}]")
        await asyncio.sleep(delay)
        self.human.on_action()
        await self.client.discard_tile(tile, moqie=is_moqie)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="雀魂自动打牌机器人")
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="配置文件路径"
    )
    args = parser.parse_args()

    bot = MajsoulBot(args.config)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: setattr(bot, '_running', False))

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
