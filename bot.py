"""雀魂自动打牌机器人 — 主入口"""
import asyncio
import logging
import signal
import sys

import ms.protocol_pb2 as pb
from google.protobuf.json_format import MessageToDict

from client import MajsoulClient
from config import load_config
from game_state import GameState
from ai.basic import BasicAI
from human_like import HumanBehavior

logger = logging.getLogger("majsoul")


class MajsoulBot:
    """机器人主控制器"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.client = MajsoulClient()
        self.ai = BasicAI()
        self.human = HumanBehavior()
        self.game_state: GameState | None = None
        self.games_played = 0
        self._running = True
        self._in_game = False
        self._game_end_event = asyncio.Event()

    async def run(self) -> None:
        """主运行循环"""
        # 设置日志
        log_level = getattr(logging, self.config.run.log_level, logging.INFO)
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        logger.info("🀄 雀魂机器人启动")

        try:
            # 连接
            await self.client.connect()

            # 登录
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
            await self.client.start_event_loop()

            # 启动心跳
            heartbeat_task = asyncio.create_task(
                self.client.heartbeat_loop()
            )

            # 主循环：匹配 → 打牌 → 等待结束 → 重复
            max_games = self.config.run.max_games
            while self._running:
                if max_games > 0 and self.games_played >= max_games:
                    logger.info(
                        f"已完成 {self.games_played} 局，停止匹配"
                    )
                    break

                # 开始匹配
                self._game_end_event.clear()
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
                await self._game_end_event.wait()
                self.games_played += 1

                # 对局间隔
                if self._running:
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

    async def _on_action(self, action_name: str, data: bytes) -> None:
        """处理对局中的操作"""
        if not self.game_state:
            logger.warning("收到操作但没有游戏状态")
            return

        gs = self.game_state

        try:
            if action_name == ".lq.ActionNewRound":
                await self._handle_new_round(data)
            elif action_name == ".lq.ActionDealTile":
                await self._handle_deal_tile(data)
            elif action_name == ".lq.ActionDiscardTile":
                await self._handle_discard_tile(data)
            elif action_name == ".lq.ActionChiPengGang":
                await self._handle_chi_peng_gang(data)
            elif action_name == ".lq.ActionAnGangAddGang":
                await self._handle_angang_addgang(data)
            elif action_name == ".lq.ActionHule":
                await self._handle_hule(data)
            elif action_name == ".lq.ActionNoTile":
                await self._handle_notile(data)
            elif action_name == ".lq.ActionLiuJu":
                await self._handle_liuju(data)
            else:
                logger.debug(f"未处理的操作: {action_name}")
        except Exception as e:
            logger.exception(f"处理操作 {action_name} 出错: {e}")

    async def _handle_new_round(self, data: bytes) -> None:
        """新一局"""
        msg = pb.RecordNewRound()
        msg.ParseFromString(data)
        d = MessageToDict(msg, preserving_proto_field_name=True)

        gs = self.game_state
        gs.new_round(d)
        self.human.on_round_start()
        self.ai.on_round_start(gs)

        # 如果有等待的操作（庄家第一巡）
        operation = d.get("operation")
        if operation:
            gs.pending_operation = operation
            await self._process_pending_operation()
        elif gs.seat == gs.dealer and gs.draw:
            # 庄家需要出牌
            await self._do_discard()

    async def _handle_deal_tile(self, data: bytes) -> None:
        """摸牌"""
        msg = pb.RecordDealTile()
        msg.ParseFromString(data)
        d = MessageToDict(msg, preserving_proto_field_name=True)

        seat = d.get("seat", 0)
        tile = d.get("tile", "")

        gs = self.game_state
        gs.on_draw(seat, tile)

        # 检查新宝牌
        doras = d.get("doras", [])
        for dora in doras:
            if dora not in gs.dora_indicators:
                gs.on_new_dora(dora)

        # 处理操作（自摸/暗杠/加杠/立直等）
        operation = d.get("operation")
        if operation and seat == gs.seat:
            gs.pending_operation = operation
            await self._process_pending_operation()
        elif seat == gs.seat:
            # 普通摸牌，出牌
            await self._do_discard()

    async def _handle_discard_tile(self, data: bytes) -> None:
        """其他人出牌"""
        msg = pb.RecordDiscardTile()
        msg.ParseFromString(data)
        d = MessageToDict(msg, preserving_proto_field_name=True)

        seat = d.get("seat", 0)
        tile = d.get("tile", "")
        is_draw = d.get("moqie", False)
        is_riichi = d.get("is_liqi", False)

        gs = self.game_state
        gs.on_discard(seat, tile, is_draw, is_riichi)

        # 是否有操作可以执行（吃碰杠荣和）
        operation = d.get("operation")
        if operation:
            gs.pending_operation = operation
            await self._process_pending_operation()

    async def _handle_chi_peng_gang(self, data: bytes) -> None:
        """吃碰杠"""
        msg = pb.RecordChiPengGang()
        msg.ParseFromString(data)
        d = MessageToDict(msg, preserving_proto_field_name=True)

        seat = d.get("seat", 0)
        type_ = d.get("type", 0)
        tiles = d.get("tiles", [])
        froms = d.get("froms", [])

        gs = self.game_state
        gs.on_chi_peng_gang(seat, type_, tiles, froms)

        # 吃碰后需要出牌
        operation = d.get("operation")
        if operation and seat == gs.seat:
            gs.pending_operation = operation
            await self._process_pending_operation()
        elif seat == gs.seat:
            await self._do_discard()

    async def _handle_angang_addgang(self, data: bytes) -> None:
        """暗杠/加杠"""
        msg = pb.RecordAnGangAddGang()
        msg.ParseFromString(data)
        d = MessageToDict(msg, preserving_proto_field_name=True)

        seat = d.get("seat", 0)
        type_ = d.get("type", 0)  # 2=暗杠, 3=加杠
        tiles = d.get("tiles", "")

        gs = self.game_state
        if type_ == 2:
            gs.on_ankan(seat, [tiles] if isinstance(tiles, str) else tiles)
        elif type_ == 3:
            gs.on_kakan(seat, tiles)

        # 可能有抢杠和
        operation = d.get("operation")
        if operation:
            gs.pending_operation = operation
            await self._process_pending_operation()

    async def _handle_hule(self, data: bytes) -> None:
        """和牌"""
        msg = pb.RecordHule()
        msg.ParseFromString(data)
        d = MessageToDict(msg, preserving_proto_field_name=True)

        hules = d.get("hules", [])
        for h in hules:
            seat = h.get("seat", 0)
            point_rong = h.get("point_rong", 0)
            point_zimo = h.get("point_zimo_qin", 0) or h.get("point_zimo_xian", 0)
            fans = h.get("fans", [])
            fan_names = [f.get("name", "") for f in fans]
            logger.info(
                f"🎉 玩家{seat}和了! "
                f"{'荣和' if point_rong else '自摸'} "
                f"役: {', '.join(fan_names)}"
            )

        # 确认进入下一局
        delay = self.human.get_new_round_delay()
        await asyncio.sleep(delay)
        await self.client.confirm_new_round()

    async def _handle_notile(self, data: bytes) -> None:
        """流局"""
        logger.info("流局")
        delay = self.human.get_new_round_delay()
        await asyncio.sleep(delay)
        await self.client.confirm_new_round()

    async def _handle_liuju(self, data: bytes) -> None:
        """中途流局（九种九牌等）"""
        logger.info("中途流局")
        delay = self.human.get_new_round_delay()
        await asyncio.sleep(delay)
        await self.client.confirm_new_round()

    async def _on_game_end(self, data: bytes) -> None:
        """对局结束"""
        self._in_game = False
        self.ai.on_game_end({})
        logger.info("=== 对局结束 ===")
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
            # 跳过 — 也要模拟思考时间
            delay = self.human.get_skip_delay()
            logger.debug(f"跳过操作 (等待 {delay:.1f}s)")
            await asyncio.sleep(delay)
            await self.client.skip_action()
            # 如果有出牌操作（type=1），需要出牌
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
            delay = self.human.get_call_delay(call)
            logger.debug(f"和牌 (等待 {delay:.1f}s)")
            await asyncio.sleep(delay)
            await self.client.win(action_type)
        elif action_type == 7:
            # 立直
            delay = self.human.get_riichi_delay()
            logger.debug(f"立直 (等待 {delay:.1f}s)")
            await asyncio.sleep(delay)
            tile = action.get("tile", "")
            if not tile:
                tile = self.ai.decide_discard(gs)
            is_moqie = (tile == gs.draw)
            await self.client.discard_tile(tile, is_riichi=True, moqie=is_moqie)
        elif action_type in [2, 3, 5]:
            # 吃碰杠
            call = {2: "chi", 3: "pon", 5: "kan"}.get(action_type, "pon")
            delay = self.human.get_call_delay(call)
            logger.debug(f"{call} (等待 {delay:.1f}s)")
            await asyncio.sleep(delay)
            combination = action.get("combination", [])
            await self.client.chi_peng_gang(action_type, combination)
        elif action_type in [4, 6]:
            # 暗杠/加杠
            delay = self.human.get_call_delay("kan")
            logger.debug(f"杠 (等待 {delay:.1f}s)")
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

        # 人类化延迟
        delay = self.human.get_discard_delay(
            is_tsumogiri=is_moqie,
            hand_size=len(gs.hand),
            is_riichi=gs.players[gs.seat].riichi,
        )
        logger.debug(f"出牌 {'摸切' if is_moqie else '手切'} (等待 {delay:.1f}s)")
        await asyncio.sleep(delay)
        self.human.on_action()
        await self.client.discard_tile(tile, moqie=is_moqie)


# 需要 import random
import random


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="雀魂自动打牌机器人")
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="配置文件路径"
    )
    args = parser.parse_args()

    bot = MajsoulBot(args.config)

    # 优雅退出
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: setattr(bot, '_running', False))

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
