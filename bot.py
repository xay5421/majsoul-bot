"""雀魂自动打牌机器人 — 主入口"""
import asyncio
import logging
import os
import random
import signal
import sys
from datetime import datetime

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
        self._discard_confirmed = False  # 服务端已确认出牌（防止竞争）
        self._live_handler = None  # 当前局的 live log handler
        self._current_game_log = None  # 当前局日志路径

    def _live(self, msg: str) -> None:
        """写入对局实况日志 (game_live.log)"""
        if hasattr(self, '_live_log'):
            self._live_log.info(msg)

    def _start_game_live_log(self) -> None:
        """为当前对局创建新的实况日志文件"""
        # 关闭上一局的 handler
        self._stop_game_live_log()

        game_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        game_num = self.games_played + 1
        log_path = f"logs/game_{game_time}_#{game_num}.log"
        self._live_handler = logging.FileHandler(log_path, encoding="utf-8")
        self._live_handler.setFormatter(logging.Formatter(
            "%(asctime)s │ %(message)s", datefmt="%H:%M:%S",
        ))
        self._live_log.addHandler(self._live_handler)
        self._current_game_log = log_path
        logger.info(f"对局日志: {log_path}")

    def _stop_game_live_log(self) -> None:
        """关闭当前对局的实况日志文件"""
        if self._live_handler:
            self._live_handler.close()
            self._live_log.removeHandler(self._live_handler)
            self._live_handler = None

    async def run(self) -> None:
        """主运行循环"""
        log_level = getattr(logging, self.config.run.log_level, logging.INFO)

        # 确保 logs 目录存在
        os.makedirs("logs", exist_ok=True)

        # 终端输出
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        # 全局文件日志 — 整个运行周期写一个文件，记录所有 logger 输出
        start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        bot_log_path = f"logs/bot_{start_time}.log"
        bot_file_handler = logging.FileHandler(bot_log_path, encoding="utf-8")
        bot_file_handler.setLevel(log_level)
        bot_file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        logging.getLogger().addHandler(bot_file_handler)

        # 对局实况日志 — 每局单独文件，在 _on_game_start 里创建
        self._live_log = logging.getLogger("majsoul.live")
        self._live_log.setLevel(logging.INFO)
        self._live_log.propagate = False  # 不传播到 root logger
        self._live_handler = None  # 当前局的 file handler

        logger.info(f"🀄 雀魂机器人启动 (日志: {bot_log_path})")

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
            self._heartbeat_task = heartbeat_task

            # 检查并重连残留对局
            reconnected = await self.client.check_and_reconnect_game()
            if reconnected:
                logger.info("已重连残留对局，等待对局结束...")
                try:
                    await asyncio.wait_for(self._game_end_event.wait(), timeout=3600)
                except asyncio.TimeoutError:
                    logger.warning("重连对局超时 (3600s)")
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

                # 等待对局结束（同时监控断线）
                logger.info("等待对局结束...")
                while not self._game_end_event.is_set():
                    # 同时等 game_end 和断线信号
                    game_end_task = asyncio.create_task(self._game_end_event.wait())
                    disconnect_task = asyncio.create_task(
                        self.client._game_disconnected.wait()
                    )
                    
                    done, pending = await asyncio.wait(
                        [game_end_task, disconnect_task],
                        timeout=3600,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    
                    for t in pending:
                        t.cancel()
                    
                    if not done:
                        # 超时 — 对局可能还在进行，继续等待
                        logger.warning("对局已运行超过 3600s，继续等待...")
                        continue
                    
                    if disconnect_task in done and not self._game_end_event.is_set():
                        # 断线了，尝试重连
                        logger.warning("⚠️ 对局中途断线，尝试自动重连...")
                        self._live("⚠️ 对局中途断线，尝试重连...")
                        reconnected = await self.client.auto_reconnect_game()
                        if not reconnected:
                            logger.error("重连失败，本局作废")
                            self._live("❌ 重连失败，本局作废")
                            break
                        # 重连成功，继续等待对局结束
                        self._live("✅ 重连成功，继续对局")
                        continue
                    
                    # game_end 触发，正常结束
                    break

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
            # 确保心跳任务被取消
            if hasattr(self, '_heartbeat_task') and self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass
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

        # 创建本局的实况日志文件
        self._start_game_live_log()

        logger.info(f"对局开始! 座位={seat}")

    async def _on_game_restore(self, game_restore) -> None:
        """断线重连 — 从 snapshot + actions 恢复游戏状态"""
        if not self.game_state:
            logger.warning("重连但没有游戏状态，无法恢复")
            return

        # 断线重连：尝试用 GameRestore actions 重放给 Mortal 同步状态
        # 如果重放失败再 fallback 到 ShantenAI
        replay_to_mortal = self._is_mortal

        # ── 从 snapshot 恢复局面状态 ──
        snapshot = game_restore.snapshot
        # Debug: 打印 GameRestore 原始结构
        from google.protobuf.json_format import MessageToDict
        restore_dict = MessageToDict(game_restore, preserving_proto_field_name=True)
        # 只打印 snapshot 部分（不打印 actions 内容）
        snap_dict = restore_dict.get('snapshot', {})
        logger.info(f"🔄 GameRestore snapshot 原始数据: {snap_dict}")
        logger.info(f"🔄 GameRestore actions 数量: {len(game_restore.actions)}")
        
        has_snapshot = snapshot and (list(snapshot.hands) or snapshot.left_tile_count > 0)
        if has_snapshot:
            logger.info(
                f"🔄 从 snapshot 恢复: 東{snapshot.chang+1}局 "
                f"ju={snapshot.ju} ben={snapshot.ben} "
                f"hands={list(snapshot.hands)} "
                f"left={snapshot.left_tile_count} "
                f"doras={list(snapshot.doras)}"
            )
            # 构造 new_round 需要的数据
            scores = []
            for p in snapshot.players:
                scores.append(int(p.score))
            
            # snapshot.hands 是自家当前手牌
            tiles_list = list(snapshot.hands)
            
            d = {
                'chang': snapshot.chang,
                'ju': snapshot.ju,
                'ben': snapshot.ben,
                'liqibang': snapshot.liqibang,
                'doras': list(snapshot.doras),
                'scores': scores,
                'tiles': tiles_list,
            }
            self.game_state.new_round(d)
            self.game_state.tiles_left = snapshot.left_tile_count
            
            # 恢复各家副露和弃牌
            for i, p_snap in enumerate(snapshot.players):
                if i < len(self.game_state.players):
                    self.game_state.players[i].discards = list(p_snap.qipais)
        else:
            logger.warning("⚠️ snapshot 为空，无法恢复局面!")

        # ── 重放增量 actions ──
        actions = game_restore.actions
        logger.info(f"🔄 重放 {len(actions)} 个动作恢复状态...")
        
        # Debug: 打印所有 action 名称概览
        action_names = [a.name for a in actions]
        logger.info(f"🔄 actions 概览: {action_names[:20]}{'...' if len(action_names) > 20 else ''}")

        for action_proto in actions:
            action_name = action_proto.name
            raw_data = action_proto.data
            
            # GameRestore 的 action data 是明文 protobuf，不需要 XOR 解码！
            # （实时推送的 ActionPrototype.data 才需要 XOR 解码）
            action_data = raw_data

            try:
                # 重放时只更新状态，不做决策
                if action_name == "ActionNewRound":
                    msg = pb.ActionNewRound()
                    msg.ParseFromString(action_data)
                    tiles_list = list(msg.tiles)
                    doras_list = list(msg.doras) or ([msg.dora] if msg.dora else [])
                    
                    # 断线重连时 tiles 可能为空，手牌在 opens 字段里
                    if not tiles_list and msg.opens:
                        for opened in msg.opens:
                            if opened.seat == self.game_state.seat:
                                tiles_list = list(opened.tiles)
                                logger.info(
                                    f"🔄 从 opens 恢复手牌: seat={opened.seat} "
                                    f"tiles={tiles_list}"
                                )
                                break
                    
                    opens_info = [{'seat': o.seat, 'tiles': list(o.tiles)} for o in msg.opens]
                    logger.info(
                        f"🔄 重放 ActionNewRound: tiles={tiles_list} "
                        f"doras={doras_list} scores={list(msg.scores)} "
                        f"chang={msg.chang} ju={msg.ju} ben={msg.ben} "
                        f"opens={opens_info}"
                    )
                    d = {
                        'chang': msg.chang, 'ju': msg.ju, 'ben': msg.ben,
                        'liqibang': msg.liqibang,
                        'doras': doras_list,
                        'scores': list(msg.scores),
                        'tiles': tiles_list,
                    }
                    self.game_state.new_round(d)
                elif action_name == "ActionDealTile":
                    msg = pb.ActionDealTile()
                    msg.ParseFromString(action_data)
                    logger.debug(
                        f"🔄 重放 DealTile: seat={msg.seat} tile={msg.tile!r} "
                        f"left={msg.left_tile_count} my_seat={self.game_state.seat}"
                    )
                    if msg.seat == self.game_state.seat and msg.tile:
                        self.game_state.on_draw(msg.seat, msg.tile)
                    elif msg.seat != self.game_state.seat:
                        self.game_state.tiles_left -= 1
                    if msg.left_tile_count:
                        self.game_state.tiles_left = msg.left_tile_count
                elif action_name == "ActionDiscardTile":
                    msg = pb.ActionDiscardTile()
                    msg.ParseFromString(action_data)
                    logger.debug(
                        f"🔄 重放 Discard: seat={msg.seat} tile={msg.tile!r} "
                        f"moqie={msg.moqie}"
                    )
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
                    if msg.type == 3:  # 暗杠
                        tiles = [msg.tiles] if isinstance(msg.tiles, str) else list(msg.tiles)
                        self.game_state.on_ankan(msg.seat, tiles)
                    elif msg.type == 2:  # 加杠
                        self.game_state.on_kakan(msg.seat, msg.tiles)
            except Exception as e:
                logger.warning(f"重放 {action_name} 出错: {e}", exc_info=True)

        # ── 重放给 Mortal 同步状态 ──
        if replay_to_mortal:
            try:
                self._replay_actions_to_mortal(actions)
                # 清除重放过程中产生的旧决策缓存
                if hasattr(self.ai, 'clear_last_reaction'):
                    self.ai.clear_last_reaction()
                logger.info("✅ Mortal 状态同步完成，无需 fallback")
            except Exception as e:
                logger.warning(f"Mortal 重放失败，fallback 到 ShantenAI: {e}")
                self.ai._force_fallback()

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
            last_data = last.data  # GameRestore 的 action data 是明文，不需要 XOR

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

    def _replay_actions_to_mortal(self, actions) -> None:
        """把 GameRestore 的 actions 转换成 mjai 事件重放给 Mortal，同步其内部状态。
        
        这样断线重连后 Mortal 能继续做决策，不需要 fallback 到 ShantenAI。
        """
        from ai.mortal import ms_to_mjai, MortalAI
        
        ai = self.ai
        if not isinstance(ai, MortalAI):
            raise RuntimeError("AI 不是 MortalAI")
        
        seat = self.game_state.seat
        _BAKAZE = {0: "E", 1: "S", 2: "W", 3: "N"}
        
        # 跟踪立直状态（哪些玩家已立直）
        riichi_accepted = set()
        _mortal_reach_pending = False
        
        for action_proto in actions:
            action_name = action_proto.name
            action_data = action_proto.data  # 明文 protobuf
            
            try:
                if action_name == "ActionNewRound":
                    msg = pb.ActionNewRound()
                    msg.ParseFromString(action_data)
                    
                    tiles_list = list(msg.tiles)
                    doras_list = list(msg.doras) or ([msg.dora] if msg.dora else [])
                    scores = list(msg.scores)
                    
                    # 庄家14张：start_kyoku 只发13张，第14张作为 tsumo
                    tsumo_tile = None
                    if len(tiles_list) == 14 and msg.ju == seat:
                        tsumo_tile = tiles_list[-1]
                        tehais_tiles = tiles_list[:13]
                    else:
                        tehais_tiles = tiles_list
                    
                    tehais = []
                    for i in range(self.game_state.player_count):
                        if i == seat:
                            tehais.append([ms_to_mjai(t) for t in tehais_tiles])
                        else:
                            tehais.append(["?"] * 13)
                    
                    dora_marker = ms_to_mjai(doras_list[0]) if doras_list else "?"
                    
                    event = {
                        "type": "start_kyoku",
                        "bakaze": _BAKAZE.get(msg.chang, "E"),
                        "dora_marker": dora_marker,
                        "kyoku": msg.ju + 1,
                        "honba": msg.ben,
                        "kyotaku": msg.liqibang,
                        "oya": msg.ju,
                        "scores": scores,
                        "tehais": tehais,
                    }
                    ai._send_and_collect(event)
                    riichi_accepted = set()
                    
                    # 庄家第14张作为 tsumo
                    if tsumo_tile:
                        tsumo_event = {
                            "type": "tsumo",
                            "actor": seat,
                            "pai": ms_to_mjai(tsumo_tile),
                        }
                        resp = ai._send_and_collect(tsumo_event)
                        # 庄家第一手也可能 reach（极端情况）
                        if resp and resp.get("type") == "reach":
                            r2 = ai._send_and_collect({"type": "reach", "actor": seat})
                            logger.debug(f"🔄→Mortal start tsumo reach → dahai: {r2}")
                            _mortal_reach_pending = True
                        else:
                            _mortal_reach_pending = False
                    
                    logger.debug(f"🔄→Mortal: start_kyoku tiles={tehais_tiles}"
                                 f"{' + tsumo=' + tsumo_tile if tsumo_tile else ''}")
                
                elif action_name == "ActionDealTile":
                    msg = pb.ActionDealTile()
                    msg.ParseFromString(action_data)
                    
                    tile = msg.tile if msg.seat == seat else None
                    event = {
                        "type": "tsumo",
                        "actor": msg.seat,
                        "pai": ms_to_mjai(tile) if tile else "?",
                    }
                    resp = ai._send_and_collect(event)
                    
                    # 只处理自家 tsumo 的 reach（不要重置他家 tsumo 的 flag）
                    if msg.seat == seat:
                        if resp and resp.get("type") == "reach":
                            # Mortal 想立直：发 reach 获取 dahai
                            r2 = ai._send_and_collect({"type": "reach", "actor": seat})
                            logger.debug(f"🔄→Mortal tsumo reach → dahai: {r2}")
                            _mortal_reach_pending = True
                        else:
                            _mortal_reach_pending = False
                    
                    logger.debug(f"🔄→Mortal tsumo(seat={msg.seat}): resp={resp}")
                    
                elif action_name == "ActionDiscardTile":
                    msg = pb.ActionDiscardTile()
                    msg.ParseFromString(action_data)
                    
                    actual_tile = ms_to_mjai(msg.tile)
                    
                    if msg.seat == seat:
                        # 自家出牌：Mortal 在 tsumo 时已经做了决策
                        # 只需发确认的 dahai + 处理立直
                        if msg.is_liqi and not _mortal_reach_pending:
                            # 历史上立直但 Mortal 没想立直
                            ai._send_and_collect({"type": "reach", "actor": msg.seat})
                        
                        event = {
                            "type": "dahai",
                            "actor": msg.seat,
                            "pai": actual_tile,
                            "tsumogiri": msg.moqie,
                        }
                        ai._send_and_collect(event)
                        
                        if msg.is_liqi or _mortal_reach_pending:
                            ai._send_and_collect({"type": "reach_accepted", "actor": msg.seat})
                        
                        _mortal_reach_pending = False
                    else:
                        # 他家出牌
                        if msg.is_liqi:
                            ai._send_and_collect({"type": "reach", "actor": msg.seat})
                        
                        event = {
                            "type": "dahai",
                            "actor": msg.seat,
                            "pai": actual_tile,
                            "tsumogiri": msg.moqie,
                        }
                        resp = ai._send_and_collect(event)
                        
                        if msg.is_liqi:
                            ai._send_and_collect({"type": "reach_accepted", "actor": msg.seat})
                        
                        logger.debug(f"🔄→Mortal dahai(seat={msg.seat}): resp={resp}")
                    
                elif action_name == "ActionLiqi":
                    # 立直宣言 — 不需要单独处理，在 DiscardTile 的 is_liqi 里处理
                    pass
                    
                elif action_name == "ActionLiqiAccepted":
                    # 立直成立
                    msg = pb.ActionDiscardTile()  # 通用解析
                    # reach_accepted 在 dahai(is_liqi) 之后自动发
                    pass
                
                elif action_name == "ActionChiPengGang":
                    msg = pb.ActionChiPengGang()
                    msg.ParseFromString(action_data)
                    
                    tiles = list(msg.tiles)
                    froms = list(msg.froms)
                    
                    if msg.type == 0:  # 吃
                        # froms 指出哪张来自哪个玩家
                        target = -1
                        pai = tiles[0]
                        for i, f in enumerate(froms):
                            if f != msg.seat:
                                target = f
                                pai = tiles[i]
                                break
                        consumed = [tiles[i] for i, f in enumerate(froms) if f == msg.seat]
                        event = {
                            "type": "chi",
                            "actor": msg.seat,
                            "target": target,
                            "pai": ms_to_mjai(pai),
                            "consumed": [ms_to_mjai(t) for t in consumed],
                        }
                    elif msg.type == 1:  # 碰
                        # froms 指出哪张来自哪个玩家
                        target_seat = -1
                        pai = tiles[0]
                        for i, f in enumerate(froms):
                            if f != msg.seat:
                                target_seat = f
                                pai = tiles[i]
                                break
                        # consumed 是 actor 手中的2张
                        consumed = [tiles[i] for i, f in enumerate(froms) if f == msg.seat]
                        
                        event = {
                            "type": "pon",
                            "actor": msg.seat,
                            "target": target_seat,
                            "pai": ms_to_mjai(pai),
                            "consumed": [ms_to_mjai(t) for t in consumed],
                        }
                    elif msg.type == 2:  # 大明杠
                        target = -1
                        pai = tiles[0]
                        for i, f in enumerate(froms):
                            if f != msg.seat:
                                target = f
                                pai = tiles[i]
                                break
                        # consumed 是 actor 手中的3张（不含被杠的那张）
                        consumed = [tiles[i] for i, f in enumerate(froms) if f == msg.seat]
                        event = {
                            "type": "daiminkan",
                            "actor": msg.seat,
                            "target": target,
                            "pai": ms_to_mjai(pai),
                            "consumed": [ms_to_mjai(t) for t in consumed],
                        }
                    else:
                        logger.debug(f"🔄→Mortal: 跳过 ChiPengGang type={msg.type}")
                        continue
                    
                    ai._send_and_collect(event)
                
                elif action_name == "ActionAnGangAddGang":
                    msg = pb.ActionAnGangAddGang()
                    msg.ParseFromString(action_data)
                    
                    # proto 只有 tiles 字段（string），没有 tile
                    tile = msg.tiles
                    
                    if msg.type == 3:  # 暗杠
                        # 处理赤牌：赤牌每种只有1张
                        from tiles import normalize_aka
                        if tile.startswith("0"):
                            normal = normalize_aka(tile)
                            consumed = [ms_to_mjai(normal)] * 3 + [ms_to_mjai(tile)]
                        elif tile in ("5m", "5p", "5s"):
                            aka = "0" + tile[1]
                            consumed = [ms_to_mjai(tile)] * 3 + [ms_to_mjai(aka)]
                        else:
                            consumed = [ms_to_mjai(tile)] * 4
                        event = {
                            "type": "ankan",
                            "actor": msg.seat,
                            "consumed": consumed,
                        }
                    elif msg.type == 2:  # 加杠
                        consumed = [ms_to_mjai(tile)] * 3  # 碰时的3张牌
                        event = {
                            "type": "kakan",
                            "actor": msg.seat,
                            "pai": ms_to_mjai(tile),
                            "consumed": consumed,
                        }
                    else:
                        logger.debug(f"🔄→Mortal: 跳过 AnGangAddGang type={msg.type}")
                        continue
                    
                    ai._send_and_collect(event)
                
                elif action_name == "ActionHule":
                    # 和牌 — 重连时不太可能出现
                    pass
                    
                elif action_name == "ActionNoTile":
                    # 流局 — 重连时不太可能出现
                    pass
                
                else:
                    logger.debug(f"🔄→Mortal: 跳过未知 action: {action_name}")
                    
            except Exception as e:
                logger.warning(f"🔄→Mortal: 重放 {action_name} 失败: {e}")
                raise  # 重放失败则整个 fallback

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

        self._discard_confirmed = False  # 新一局重置

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
        self._discard_confirmed = False  # 重置出牌确认标记
        display.show_draw(gs, tile)
        self._live(
            f"  摸 {tile_to_str(tile)} "
            f"| 手牌: {tiles_to_str(sort_tiles(gs.hand))} + {tile_to_str(tile)} "
            f"| 剩{gs.tiles_left}枚"
        )

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
            # 立直后不需要主动出牌（server 会自动摸切）
            if gs.players[gs.seat].riichi:
                logger.debug("立直中，等待服务端自动摸切")
            else:
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
            self._discard_confirmed = True  # 标记已出牌
            # 清除旧的 Mortal 决策缓存，防止重复使用
            if self._is_mortal and hasattr(self.ai, 'clear_last_reaction'):
                self.ai.clear_last_reaction()

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
                f"| 手牌: {tiles_to_str(sort_tiles(gs.hand))} "
                f"| 剩{gs.tiles_left}枚"
            )
            return  # 自己出的牌不需要响应

        # 他家出牌写入 live log
        moqie = " (摸切)" if is_draw else ""
        riichi = " [立直!]" if is_riichi else ""
        self._live(f"[巡{gs.turn:2d}] P{seat}打: {tile_to_str(tile)}{moqie}{riichi}")

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

        # 自家副露后需要出牌，重置确认标记
        if seat == gs.seat:
            self._discard_confirmed = False
            # 清除 Mortal 决策缓存，防止副露后出牌时使用旧决策
            if self._is_mortal and hasattr(self.ai, 'clear_last_reaction'):
                self.ai.clear_last_reaction()

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
        type_ = msg.type  # 3=暗杠, 2=加杠
        tiles_str = msg.tiles

        gs = self.game_state

        if type_ == 3:  # 暗杠
            gs.on_ankan(seat, [tiles_str] if isinstance(tiles_str, str) else list(tiles_str))
            if self._is_mortal:
                # 暗杠 consumed 需要正确处理赤牌：
                # 赤牌 (0m/0p/0s) 每种只有1张，暗杠时应该是 3张普通 + 1张赤
                from tiles import normalize_aka
                if tiles_str.startswith("0"):
                    # 赤牌暗杠: 1张赤 + 3张普通 (如 0m → ["5m","5m","5m","0m"])
                    normal = normalize_aka(tiles_str)  # "0m" → "5m"
                    consumed = [normal, normal, normal, tiles_str]
                elif tiles_str in ("5m", "5p", "5s"):
                    # 普通五暗杠: 可能含赤牌，但服务端传的 tile 是普通五
                    # 保守处理：3张普通 + 1张赤（四人麻将一定有赤牌）
                    aka = "0" + tiles_str[1]  # "5m" → "0m"
                    consumed = [tiles_str, tiles_str, tiles_str, aka]
                else:
                    consumed = [tiles_str] * 4
                self.ai.send_ankan(seat, consumed)
        elif type_ == 2:  # 加杠
            # 加杠：从碰的 meld 中获取 consumed
            # mjai 协议要求 kakan 的 consumed 是碰时的 3 张牌
            pon_consumed = [tiles_str, tiles_str, tiles_str]  # 默认同名牌x3
            for m in gs.players[seat].melds:
                if m.type == "碰" and tiles_str in m.tiles:
                    pon_consumed = list(m.tiles)[:3]
                    break
            gs.on_kakan(seat, tiles_str)
            if self._is_mortal:
                self.ai.send_kakan(seat, tiles_str, pon_consumed)

        who = '我' if seat == gs.seat else f'玩家{seat}'
        names = {3: '暗杠', 2: '加杠'}
        kan_name = names.get(type_, f'杠{type_}')
        logger.info(f"{who} {kan_name}: {tiles_str} (raw type={type_})")
        self._live(f"[巡{gs.turn:2d}] {who} {kan_name}: {tile_to_str(tiles_str)}")

        # 自家杠操作后清除 Mortal 决策缓存，防止岭上摸牌时重复使用旧决策
        if seat == gs.seat and self._is_mortal and hasattr(self.ai, 'clear_last_reaction'):
            self.ai.clear_last_reaction()

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
            # 获取最后一局的原始分数（来自 game_state）
            raw_scores = None
            if self.game_state and hasattr(self.game_state, 'scores'):
                raw_scores = list(self.game_state.scores)
            # 写入实况日志 - scores 是 uma 调整后的最终分数
            ranked = sorted(enumerate(scores), key=lambda x: -x[1])
            lines = ["🏁 对局结束!"]
            if raw_scores:
                lines.append(f"  原始分数: " + " / ".join(f"P{i}:{raw_scores[i]}" for i in range(len(raw_scores))))
            lines.append(f"  最终得点 (uma调整后):")
            for rank, (i, sc) in enumerate(ranked):
                me = " ← 自家" if self.game_state and i == self.game_state.seat else ""
                lines.append(f"    第{rank+1}名 P{i}: {sc:+d}{me}")
            self._live("\n         │ ".join(lines))
        except Exception:
            logger.info("🏁 对局结束!")

        self.ai.on_game_end({})
        self._stop_game_live_log()
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
            # 跳过 — 不加 delay，直接发送，给后续出牌留时间
            display.show_action_decision("skip")
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

        # 检查服务端是否已经替我们出牌（超时自动摸切）
        if self._discard_confirmed:
            logger.info("服务端已确认出牌，跳过本次出牌")
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
        # delay 之后再检查一次（防止 delay 期间服务端超时自动出牌）
        if self._discard_confirmed:
            logger.info("delay 期间服务端已出牌，取消本次出牌")
            return
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

    def _shutdown():
        bot._running = False
        # 取消心跳 task，让事件循环能退出
        if hasattr(bot, '_heartbeat_task') and bot._heartbeat_task:
            bot._heartbeat_task.cancel()
        # 设置 game_end 让主循环的 wait 退出
        bot._game_end_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
