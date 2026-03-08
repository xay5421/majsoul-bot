"""雀魂自动打牌机器人 — 主入口"""
import asyncio
import datetime
import logging
import os
import random
import signal
import sys

import ms.protocol_pb2 as pb
from google.protobuf.json_format import MessageToDict

from client import MajsoulClient, MatchError1023, _get_git_commit
from codec import decode as xor_decode
from config import load_config
from game_state import GameState
from ai.basic import BasicAI
from ai.shanten import ShantenAI, calc_shanten
from human_like import HumanBehavior
import display
from tiles import tile_to_str, tiles_to_str, sort_tiles

logger = logging.getLogger("majsoul")

WIND = ['东', '南', '西', '北']


def _shanten_str(tiles: list[str], num_melds: int = 0) -> str:
    """计算向听数并返回格式化字符串
    
    Args:
        tiles: 手牌列表（不含副露中的牌）
        num_melds: 副露数（吃/碰/明杠/暗杠各算 1 个）
    """
    try:
        s = calc_shanten(tiles, num_melds)
        if s == -1:
            return "和了"
        elif s == 0:
            return "听牌"
        else:
            return f"{s}向听"
    except Exception:
        return "?"


def _create_ai(config):
    """根据配置创建 AI 实例"""
    ai_type = config.ai.type
    if ai_type == "mortal":
        from ai.mortal import MortalAI
        mortal_dir = getattr(config.ai, 'mortal_dir', None) or None
        mortal_weights = getattr(config.ai, 'mortal_weights', None) or None
        return MortalAI(mortal_dir, mortal_weights)
    elif ai_type == "shanten":
        return ShantenAI()
    else:
        return BasicAI()


class MajsoulBot:
    """机器人主控制器"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.client = MajsoulClient()
        # 传递遥测配置
        telemetry_cfg = getattr(self.config, 'telemetry', None)
        if telemetry_cfg and not getattr(telemetry_cfg, 'enabled', True):
            self.client.telemetry._enabled = False
        self.ai = _create_ai(self.config)
        ai_type = self.config.ai.type
        logger.info(f"AI 引擎: {ai_type}")
        self.human = HumanBehavior()
        self.game_state: GameState | None = None
        self.games_played = 0
        self._running = True
        self._shutdown_event = asyncio.Event()  # Ctrl+C / SIGTERM 时 set
        self._in_game = False
        self._game_end_event = asyncio.Event()
        self._is_mortal = (ai_type == "mortal")
        self._discard_event = asyncio.Event()  # 服务端确认出牌时 set
        self._discard_confirmed = False  # 兼容：服务端已确认出牌
        self._action_lock = asyncio.Lock()  # action handler 串行锁
        self._live_handler = None  # 当前局的 live log handler
        # 装弱：第一名时前 N 步用 q_values 带权随机
        self._nerf_turns = getattr(self.config.ai, 'nerf_turns', 0)
        self._nerf_active = False  # 本次出牌是否为装弱采样
        self._noise_rate = getattr(self.config.ai, 'noise_rate', 0.0)
        self._noise_temperature = getattr(self.config.ai, 'noise_temperature', 2.0)
        self._current_game_log = None  # 当前局日志路径

    async def _interruptible_sleep(self, seconds: float) -> bool:
        """可被 Ctrl+C 中断的 sleep。返回 True 表示正常完成，False 表示被中断。"""
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=seconds)
            return False  # shutdown event 被 set → 中断
        except asyncio.TimeoutError:
            return True   # 超时 → 正常完成

    def _live(self, msg: str) -> None:
        """写入对局实况日志 (game_live.log)"""
        if hasattr(self, '_live_log'):
            self._live_log.info(msg)

    def _start_game_live_log(self) -> None:
        """为当前对局创建新的实况日志文件"""
        # 关闭上一局的 handler
        self._stop_game_live_log()

        game_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
        start_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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

        git_commit = _get_git_commit()
        logger.info(f"🀄 雀魂机器人启动 (commit: {git_commit}, 日志: {bot_log_path})")

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

            # 启动心跳（注册到集中任务管理）
            heartbeat_task = self.client.create_background_task(
                self.client.heartbeat_loop(), name="heartbeat"
            )
            self._heartbeat_task = heartbeat_task

            # 检查并重连残留对局
            reconnected = await self.client.check_and_reconnect_game()
            if reconnected:
                logger.info("已重连残留对局，等待对局结束...")
                # 和主循环一样：同时监听 game_end 和断线信号
                await self._wait_for_game_end_or_disconnect()
                self.games_played += 1
                # 对局结束后刷新段位信息
                try:
                    rank = await self.client.fetch_rank_info()
                    logger.info(f"📊 当前段位: {rank} (已完成 {self.games_played} 局)")
                except Exception as e:
                    logger.warning(f"刷新段位失败: {e}")
                logger.info(f"残留对局处理完毕: _running={self._running}, max_games={self.config.run.max_games}")
                if self._running and self.config.run.max_games != 1:
                    interval = self.human.get_game_interval(self.config.run)
                    logger.info(f"等待 {interval:.0f}s ({interval/60:.1f}分钟) 后继续...")
                    if not await self._interruptible_sleep(interval):
                        self._running = False
            else:
                # 重连失败，可能 token 永久失效
                # 轮询等待残留对局自然结束，然后再开始匹配
                gi = await self.client.lobby.fetch_gaming_info(pb.ReqCommon())
                gd = MessageToDict(gi, preserving_proto_field_name=True)
                game_info = gd.get("game_info", {})
                if game_info.get("connect_token"):
                    logger.warning("残留对局仍在，token 已失效，等待对局自然结束...")
                    self._live("⏳ 残留对局 token 失效，等待自然结束...")
                    cleared = await self.client.wait_for_residual_game_to_end(
                        poll_interval=15, timeout=900
                    )
                    if not cleared:
                        logger.warning("等待残留对局超时，重新登录检查...")
                        try:
                            if await self.client.reconnect_lobby():
                                gi2 = await self.client.lobby.fetch_gaming_info(pb.ReqCommon())
                                gd2 = MessageToDict(gi2, preserving_proto_field_name=True)
                                if gd2.get("game_info", {}).get("connect_token"):
                                    logger.warning("重新登录后对局仍在（可能是幽灵对局），强制进入主循环尝试匹配")
                                else:
                                    logger.info("重新登录后对局已结束，继续")
                            else:
                                logger.error("重新登录失败，退出")
                                return
                        except Exception as e:
                            logger.error(f"重新登录失败: {e}，退出")
                            return

            # 主循环
            max_games = self.config.run.max_games
            match_mode = self.config.match.mode
            run_cfg = self.config.run
            logger.info(f"进入主循环: max_games={max_games}, mode={match_mode}, _running={self._running}")
            telemetry_enabled = getattr(getattr(self.config, 'telemetry', None), 'enabled', True)
            logger.info(
                f"🛡️ 反检测: interval={run_cfg.game_interval_min}-{run_cfg.game_interval_max}s, "
                f"session={run_cfg.session_games_min}-{run_cfg.session_games_max}局, "
                f"break={run_cfg.session_break_min//60}-{run_cfg.session_break_max//60}min, "
                f"hours={run_cfg.active_hour_start}:00-{run_cfg.active_hour_end}:00, "
                f"night_stop={'ON' if run_cfg.night_stop else 'OFF'}, "
                f"SLS遥测={'ON' if telemetry_enabled else 'OFF'}, "
                f"扰动={self._noise_rate*100:.0f}%/T={self._noise_temperature}"
            )
            while self._running:
                if max_games > 0 and self.games_played >= max_games:
                    logger.info(f"已完成 {self.games_played} 局，停止匹配")
                    break

                # ── 反检测: 活跃时段检查 ──
                if run_cfg.night_stop and not self.human.is_active_hours(run_cfg):
                    now = datetime.datetime.now()
                    logger.info(f"🌙 当前 {now.strftime('%H:%M')} 不在活跃时段 "
                                f"({run_cfg.active_hour_start}:00-{run_cfg.active_hour_end}:00)，等待...")
                    # 计算到活跃时段开始的秒数
                    target_hour = run_cfg.active_hour_start
                    target = now.replace(hour=target_hour, minute=random.randint(0, 30),
                                         second=random.randint(0, 59), microsecond=0)
                    if target <= now:
                        target += datetime.timedelta(days=1)
                    wait_secs = (target - now).total_seconds()
                    logger.info(f"🌙 将在 {target.strftime('%H:%M')} 恢复 (等待 {wait_secs/60:.0f} 分钟)")
                    if not await self._interruptible_sleep(wait_secs):
                        break
                    continue

                # ── 反检测: session 休息 ──
                if self.human.should_take_session_break(run_cfg):
                    break_time = self.human.get_session_break(run_cfg)
                    logger.info(f"☕ Session 休息 {break_time:.0f}s ({break_time/60:.1f}分钟)...")
                    if not await self._interruptible_sleep(break_time):
                        break
                    logger.info("☕ 休息结束，继续")

                self._game_end_event.clear()

                # ── 反检测: 匹配前模拟大厅行为 ──
                try:
                    # 模拟大厅浏览
                    lobby_time = self.human.get_lobby_stay_time()
                    logger.info(f"🏠 模拟大厅浏览 {lobby_time:.1f}s...")
                    await self.client.simulate_lobby_browse(lobby_time)

                    # 模拟打开匹配 UI
                    browse_time = self.human.get_match_ui_browse_time()
                    await self.client.simulate_match_ui(browse_time)
                except Exception as e:
                    logger.debug(f"UI 模拟失败 (可忽略): {e}")

                if match_mode == "ai":
                    room_id = await self.client.create_ai_room(
                        room_type=self.config.match.room_type,
                    )
                    if not room_id:
                        logger.error("创建房间失败，等待重试...")
                        if not await self._interruptible_sleep(10):
                            break
                        continue

                    success = await self.client.start_room()
                    if not success:
                        logger.error("开始对局失败")
                        if not await self._interruptible_sleep(10):
                            break
                        continue
                else:
                    try:
                        success = await self.client.match(
                            room_type=self.config.match.room_type,
                            level=self.config.match.level,
                        )
                    except MatchError1023:
                        self._match1023_count = getattr(self, '_match1023_count', 0) + 1
                        logger.warning(f"账号仍在对局中 (1023)，第 {self._match1023_count} 次，尝试重连残留对局...")
                        reconnected = await self.client.check_and_reconnect_game()
                        if reconnected:
                            logger.info("已重连残留对局，等待对局结束...")
                            self._match1023_count = 0
                            # 直接进入下面的等待对局结束逻辑
                            success = True
                        else:
                            # 重连失败 — 可能 token 永久失效，等对局自然结束
                            if self._match1023_count >= 3:
                                # 已经试了 3 轮，大概率是死局
                                # 用 polling 等待对局自然结束
                                logger.error("重连多次失败，轮询等待对局自然结束...")
                                self._live("⏳ 残留对局无法重连，等待自然结束...")
                                cleared = await self.client.wait_for_residual_game_to_end(
                                    poll_interval=15, timeout=900
                                )
                                if cleared:
                                    self._match1023_count = 0
                                    logger.info("残留对局已结束，恢复匹配")
                                    continue
                                else:
                                    logger.warning("等待残留对局超时，重新登录检查...")
                                    try:
                                        if await self.client.reconnect_lobby():
                                            gi2 = await self.client.lobby.fetch_gaming_info(pb.ReqCommon())
                                            gd2 = MessageToDict(gi2, preserving_proto_field_name=True)
                                            if gd2.get("game_info", {}).get("connect_token"):
                                                logger.warning("重新登录后对局仍在（幽灵对局），重置计数继续匹配")
                                            else:
                                                logger.info("重新登录后对局已结束，恢复匹配")
                                            self._match1023_count = 0
                                            continue
                                        else:
                                            logger.error("重新登录失败，退出")
                                            break
                                    except Exception as e:
                                        logger.error(f"重新登录失败: {e}，退出")
                                        break
                            else:
                                wait = min(30 * (2 ** (self._match1023_count - 1)), 120)
                                logger.error(f"重连残留对局也失败，等 {wait} 秒再试...")
                                if not await self._interruptible_sleep(wait):
                                    break
                            continue
                    if not success:
                        logger.error("匹配失败，等待重试...")
                        if not await self._interruptible_sleep(10):
                            break
                        continue

                # 等待对局结束（同时监控断线）
                self._match1023_count = 0  # 成功进入对局，重置计数
                
                # 如果还没进入对局（匹配请求已发但还没收到 NotifyMatchGameStart），
                # 先等待匹配完成，超时则取消重试
                if not self._in_game:
                    match_timeout = 120  # 匹配超时 120 秒
                    logger.info(f"等待匹配完成... (超时 {match_timeout}s)")
                    elapsed = 0
                    poll_interval = 5
                    while (elapsed < match_timeout 
                           and not self._in_game 
                           and not self._game_end_event.is_set()
                           and not self.client._game_disconnected.is_set()
                           and self._running):
                        await asyncio.sleep(poll_interval)
                        elapsed += poll_interval
                    if not self._running:
                        break
                    if not self._in_game and not self._game_end_event.is_set():
                        if self.client._game_disconnected.is_set():
                            logger.warning("匹配后连接对局服务器失败，重试匹配...")
                            self.client._game_disconnected.clear()
                        else:
                            logger.warning(f"匹配超时 ({match_timeout}s)，取消匹配重试...")
                        try:
                            await self.client.cancel_match()
                        except Exception as e:
                            logger.debug(f"取消匹配失败 (可忽略): {e}")
                        await asyncio.sleep(3)
                        continue
                
                await self._wait_for_game_end_or_disconnect()

                self.games_played += 1

                # 对局结束后刷新段位信息
                try:
                    rank = await self.client.fetch_rank_info()
                    logger.info(f"📊 当前段位: {rank} (已完成 {self.games_played} 局)")
                except Exception as e:
                    logger.warning(f"刷新段位失败: {e}")

                if self._running and max_games != 1:
                    # ── 反检测: 对局结束后模拟行为 ──
                    try:
                        await self.client.simulate_post_game()
                    except Exception as e:
                        logger.debug(f"结算 UI 模拟失败 (可忽略): {e}")

                    # ── 反检测: 拟人化局间间隔 ──
                    interval = self.human.get_game_interval(run_cfg)
                    logger.info(f"等待 {interval:.0f}s ({interval/60:.1f}分钟) 后继续...")
                    if not await self._interruptible_sleep(interval):
                        break

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

    async def _wait_for_game_end_or_disconnect(self) -> None:
        """等待对局结束，同时监听断线并自动重连"""
        # 确保断线信号是 clear 状态（防止上一局遗留）
        self.client._game_disconnected.clear()
        logger.info(f"等待对局结束... (game_end={self._game_end_event.is_set()}, disconnected={self.client._game_disconnected.is_set()})")
        while not self._game_end_event.is_set():
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
                logger.warning("对局已运行超过 3600s，继续等待...")
                continue

            if disconnect_task in done and not self._game_end_event.is_set():
                logger.warning(f"⚠️ 对局中途断线，尝试自动重连... (game_end={self._game_end_event.is_set()})")
                self._live("⚠️ 对局中途断线，尝试重连...")
                reconnected = await self.client.auto_reconnect_game()
                if not reconnected:
                    logger.error("重连失败，本局作废")
                    self._live("❌ 重连失败，本局作废")
                    break
                self._live("✅ 重连成功，继续对局")
                continue

            # game_end_task triggered or both triggered
            logger.info(f"等待循环退出: game_end={game_end_task in done}, disconnect={disconnect_task in done}")
            break

    async def _on_game_start(self, seat: int, auth_res) -> None:
        """对局开始"""
        player_count = 4 if "4" in self.config.match.room_type else 3
        self.game_state = GameState(seat, player_count)
        self._in_game = True
        self.human.on_game_start()
        self.ai.on_game_start(self.game_state)

        # 创建本局的实况日志文件
        self._start_game_live_log()

        # 显示对局玩家信息
        try:
            res_dict = MessageToDict(auth_res, preserving_proto_field_name=True)
            players = res_dict.get("players", [])
            if players:
                p_info = []
                for i, p in enumerate(players):
                    nick = p.get("nickname", "?")
                    lvl = p.get("level", {})
                    lvl_id = lvl.get("id", 0)
                    lvl_score = lvl.get("score", 0)
                    from client import format_rank
                    rank_str = format_rank(lvl_id, lvl_score) if lvl_id else "?"
                    me = " ★" if i == seat else ""
                    p_info.append(f"P{i}: {nick} [{rank_str}]{me}")
                logger.info("对局玩家: " + " | ".join(p_info))
        except Exception as e:
            logger.debug(f"解析玩家信息失败: {e}")

        logger.info(f"对局开始! 座位={seat}")

    async def _on_game_restore(self, game_restore) -> None:
        """断线重连 — 从 actions 恢复游戏状态
        
        使用 _action_lock 串行化，防止和实时 action 并发。
        """
        async with self._action_lock:
            await self._on_game_restore_inner(game_restore)

    async def _on_game_restore_inner(self, game_restore) -> None:
        """_on_game_restore 的实际实现（在 action_lock 内）"""
        if not self.game_state:
            logger.warning("重连但没有游戏状态，无法恢复")
            return

        replay_to_mortal = self._is_mortal
        actions = game_restore.actions
        logger.info(f"🔄 GameRestore: {len(actions)} 个动作")

        # ── 重放 actions 恢复 game_state ──
        for action_proto in actions:
            action_name = action_proto.name
            action_data = action_proto.data  # 明文 protobuf，不需要 XOR

            try:
                if action_name == "ActionNewRound":
                    msg = pb.ActionNewRound()
                    msg.ParseFromString(action_data)
                    tiles_list = list(msg.tiles)
                    doras_list = list(msg.doras) or ([msg.dora] if msg.dora else [])
                    if not tiles_list and msg.opens:
                        for opened in msg.opens:
                            if opened.seat == self.game_state.seat:
                                tiles_list = list(opened.tiles)
                                break
                    self.game_state.new_round({
                        'chang': msg.chang, 'ju': msg.ju, 'ben': msg.ben,
                        'liqibang': msg.liqibang, 'doras': doras_list,
                        'scores': list(msg.scores), 'tiles': tiles_list,
                    })
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
                    self.game_state.on_discard(msg.seat, msg.tile, msg.moqie, msg.is_liqi)
                elif action_name == "ActionChiPengGang":
                    msg = pb.ActionChiPengGang()
                    msg.ParseFromString(action_data)
                    self.game_state.on_chi_peng_gang(msg.seat, msg.type, list(msg.tiles), list(msg.froms))
                elif action_name == "ActionAnGangAddGang":
                    msg = pb.ActionAnGangAddGang()
                    msg.ParseFromString(action_data)
                    if msg.type == 3:
                        self.game_state.on_ankan(msg.seat, [msg.tiles] if isinstance(msg.tiles, str) else list(msg.tiles))
                    elif msg.type == 2:
                        self.game_state.on_kakan(msg.seat, msg.tiles)
            except Exception as e:
                logger.warning(f"重放 {action_name} 出错: {e}", exc_info=True)

        # ── 重放给 Mortal ──
        if replay_to_mortal:
            try:
                # 关键：重启 Mortal 进程，清空旧状态
                # 断线重连时 Mortal 子进程仍持有断线前的内部状态，
                # GameRestore 会从头重放所有动作，如果不重启就会状态冲突
                logger.info("🔄 重启 Mortal 进程以清空旧状态...")
                self.ai._restart_mortal()
                self.ai._send_and_collect({"type": "start_game"})
                self.ai._game_active = True
                self._replay_actions_to_mortal(actions)
                logger.info("✅ Mortal 状态同步完成")
            except Exception as e:
                logger.warning(f"Mortal 重放失败，fallback: {e}")
                self.ai._force_fallback()

        gs = self.game_state
        logger.info(
            f"✅ 恢复完成: {tiles_to_str(sort_tiles(gs.hand))}"
            f"{' + ' + tile_to_str(gs.draw) if gs.draw else ''}"
            f" | 剩{gs.tiles_left}"
        )
        display.show_round_start(gs)

        # 恢复后检查最后一个 action 是否需要我们响应
        if actions:
            last = actions[-1]
            last_name = last.name
            last_data = last.data

            try:
                if last_name == "ActionDealTile":
                    msg = pb.ActionDealTile()
                    msg.ParseFromString(last_data)
                    if msg.seat == self.game_state.seat:
                        # 等一下确认服务端没有替我们出牌
                        logger.info("🔄 重连后等待 1s 确认服务端状态...")
                        await asyncio.sleep(1)
                        if not self.game_state.draw:
                            logger.info("🔄 服务端已替我们出牌，跳过")
                        elif msg.operation and msg.operation.operation_list:
                            op = MessageToDict(msg.operation, preserving_proto_field_name=True)
                            self.game_state.pending_operation = op
                            await self._process_pending_operation()
                        else:
                            await self._do_discard()
                elif last_name == "ActionDiscardTile":
                    msg = pb.ActionDiscardTile()
                    msg.ParseFromString(last_data)
                    if msg.seat != self.game_state.seat and msg.operation and msg.operation.operation_list:
                        await asyncio.sleep(0.5)
                        op = MessageToDict(msg.operation, preserving_proto_field_name=True)
                        self.game_state.pending_operation = op
                        await self._process_pending_operation()
            except Exception as e:
                logger.warning(f"重连后恢复操作失败: {e}")

    def _replay_actions_to_mortal(self, actions) -> None:
        """把 GameRestore actions 转成 mjai 事件重放给 Mortal"""
        from ai.mortal import ms_to_mjai, MortalAI
        
        ai = self.ai
        if not isinstance(ai, MortalAI):
            raise RuntimeError("AI 不是 MortalAI")
        
        ai._mjai_log = []
        seat = self.game_state.seat
        _BAKAZE = {0: "E", 1: "S", 2: "W", 3: "N"}
        _mortal_reach_pending = False
        
        for action_proto in actions:
            name = action_proto.name
            data = action_proto.data
            
            try:
                if name == "ActionNewRound":
                    msg = pb.ActionNewRound()
                    msg.ParseFromString(data)
                    tiles = list(msg.tiles)
                    doras = list(msg.doras) or ([msg.dora] if msg.dora else [])
                    
                    tsumo_tile = None
                    if len(tiles) == 14 and msg.ju == seat:
                        tsumo_tile = tiles[-1]
                        tiles = tiles[:13]
                    
                    tehais = []
                    for i in range(self.game_state.player_count):
                        if i == seat:
                            tehais.append([ms_to_mjai(t) for t in tiles])
                        else:
                            tehais.append(["?"] * 13)
                    
                    ai._send_and_collect({
                        "type": "start_kyoku",
                        "bakaze": _BAKAZE.get(msg.chang, "E"),
                        "dora_marker": ms_to_mjai(doras[0]) if doras else "?",
                        "kyoku": msg.ju + 1,
                        "honba": msg.ben,
                        "kyotaku": msg.liqibang,
                        "oya": msg.ju,
                        "scores": list(msg.scores),
                        "tehais": tehais,
                    })
                    
                    if tsumo_tile:
                        resp = ai._send_and_collect({
                            "type": "tsumo", "actor": seat,
                            "pai": ms_to_mjai(tsumo_tile),
                        })
                        if resp and resp.get("type") == "reach":
                            ai._send_and_collect({"type": "reach", "actor": seat})
                            _mortal_reach_pending = True
                        else:
                            _mortal_reach_pending = False
                
                elif name == "ActionDealTile":
                    msg = pb.ActionDealTile()
                    msg.ParseFromString(data)
                    tile = msg.tile if msg.seat == seat else None
                    resp = ai._send_and_collect({
                        "type": "tsumo", "actor": msg.seat,
                        "pai": ms_to_mjai(tile) if tile else "?",
                    })
                    if msg.seat == seat:
                        if resp and resp.get("type") == "reach":
                            ai._send_and_collect({"type": "reach", "actor": seat})
                            _mortal_reach_pending = True
                        else:
                            _mortal_reach_pending = False
                    
                elif name == "ActionDiscardTile":
                    msg = pb.ActionDiscardTile()
                    msg.ParseFromString(data)
                    actual = ms_to_mjai(msg.tile)
                    
                    if msg.seat == seat:
                        if msg.is_liqi and not _mortal_reach_pending:
                            ai._send_and_collect({"type": "reach", "actor": seat})
                        ai._send_and_collect({
                            "type": "dahai", "actor": seat,
                            "pai": actual, "tsumogiri": msg.moqie,
                        })
                        if msg.is_liqi or _mortal_reach_pending:
                            ai._send_and_collect({"type": "reach_accepted", "actor": seat})
                        _mortal_reach_pending = False
                    else:
                        if msg.is_liqi:
                            ai._send_and_collect({"type": "reach", "actor": msg.seat})
                        ai._send_and_collect({
                            "type": "dahai", "actor": msg.seat,
                            "pai": actual, "tsumogiri": msg.moqie,
                        })
                        if msg.is_liqi:
                            ai._send_and_collect({"type": "reach_accepted", "actor": msg.seat})
                
                elif name == "ActionChiPengGang":
                    msg = pb.ActionChiPengGang()
                    msg.ParseFromString(data)
                    tiles = list(msg.tiles)
                    froms = list(msg.froms)
                    
                    target = -1
                    pai = tiles[0]
                    for i, f in enumerate(froms):
                        if f != msg.seat:
                            target = f
                            pai = tiles[i]
                            break
                    consumed = [tiles[i] for i, f in enumerate(froms) if f == msg.seat]
                    
                    type_map = {0: "chi", 1: "pon", 2: "daiminkan"}
                    etype = type_map.get(msg.type)
                    if etype:
                        ai._send_and_collect({
                            "type": etype, "actor": msg.seat, "target": target,
                            "pai": ms_to_mjai(pai),
                            "consumed": [ms_to_mjai(t) for t in consumed],
                        })
                
                elif name == "ActionAnGangAddGang":
                    msg = pb.ActionAnGangAddGang()
                    msg.ParseFromString(data)
                    tile = msg.tiles
                    
                    if msg.type == 3:  # 暗杠
                        from tiles import normalize_aka
                        if tile.startswith("0"):
                            normal = normalize_aka(tile)
                            consumed = [ms_to_mjai(normal)] * 3 + [ms_to_mjai(tile)]
                        elif tile in ("5m", "5p", "5s"):
                            consumed = [ms_to_mjai(tile)] * 3 + [ms_to_mjai("0" + tile[1])]
                        else:
                            consumed = [ms_to_mjai(tile)] * 4
                        ai._send_and_collect({"type": "ankan", "actor": msg.seat, "consumed": consumed})
                    elif msg.type == 2:  # 加杠
                        ai._send_and_collect({
                            "type": "kakan", "actor": msg.seat,
                            "pai": ms_to_mjai(tile),
                            "consumed": [ms_to_mjai(tile)] * 3,
                        })
                    
            except Exception as e:
                logger.warning(f"Mortal 重放 {name} 失败: {e}")
                raise

    async def _on_action(self, action_name: str, data: bytes) -> None:
        """处理对局中的操作 (data 已经过 XOR 解密)
        
        使用 _action_lock 串行化，因为 MSRPCChannel 用 create_task
        分发 hook，多个 action 可能并发到达。
        """
        async with self._action_lock:
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
        self._discard_event.clear()

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
            # 新一局开始时等一下再操作，防止服务端还没准备好接收
            await asyncio.sleep(2.0)
            await self._process_pending_operation()
        elif len(d['tiles']) == 14:
            # 庄家需要出牌 — 等一下再发，防止服务端丢弃请求
            await asyncio.sleep(2.0)
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
        self._discard_event.clear()
        display.show_draw(gs, tile)
        # 摸牌后手牌 = gs.hand（已含摸的牌）
        hand_with_draw = list(gs.hand)
        num_melds = len(gs.players[gs.seat].melds)
        shanten_info = _shanten_str(hand_with_draw, num_melds)
        self._live(
            f"  摸 {tile_to_str(tile)} "
            f"| 手牌: {tiles_to_str(sort_tiles(gs.hand))} + {tile_to_str(tile)} "
            f"| {shanten_info} | 剩{gs.tiles_left}枚"
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
            self._discard_event.set()
            # 清除旧的 Mortal 决策缓存，防止重复使用
            if self._is_mortal and hasattr(self.ai, 'clear_last_reaction'):
                self.ai.clear_last_reaction()

        gs = self.game_state

        # 通知 Mortal AI（包括立直宣言，所有玩家的出牌都要通知）
        if self._is_mortal:
            if is_riichi:
                self.ai.send_reach(seat)
            
            # 检测自家出牌不一致：装弱/服务端超时摸切导致实际出牌和 Mortal 决策不同
            if seat == gs.seat and hasattr(self.ai, '_intended_tile') and self.ai._intended_tile:
                from ai.mortal import ms_to_mjai, mjai_to_ms
                intended = self.ai._intended_tile
                actual_mjai = ms_to_mjai(tile)
                if intended != actual_mjai:
                    if self._nerf_active:
                        logger.debug(
                            f"🤡 装弱修正: Mortal想打={intended} 实际={actual_mjai}"
                        )
                    else:
                        logger.warning(
                            f"⚠️ 出牌不一致! Mortal想打={intended} 服务端实际={actual_mjai}({tile}) "
                            f"→ 直接喂正确的 dahai 给 Mortal"
                        )
                    # 直接把实际出的牌告诉 Mortal，不需要重启/重放
                    # Mortal 内部状态会根据这个 dahai 事件自我修正
                    self.ai.send_dahai(seat, tile, is_draw)
                    self.ai._intended_tile = None
                else:
                    self.ai._intended_tile = None
                    self.ai.send_dahai(seat, tile, is_draw)
                self._nerf_active = False
            else:
                self.ai.send_dahai(seat, tile, is_draw)
            
            if is_riichi:
                self.ai.send_reach_accepted(seat)

        gs.on_discard(seat, tile, is_draw, is_riichi)
        display.show_discard(gs, seat, tile, is_tsumogiri=is_draw, is_riichi=is_riichi)

        if seat == gs.seat:
            moqie = " (摸切)" if is_draw else ""
            riichi = " [立直]" if is_riichi else ""
            shanten_info = _shanten_str(gs.hand, len(gs.players[gs.seat].melds))
            self._live(
                f"[巡{gs.turn:2d}] 我打: {tile_to_str(tile)}{moqie}{riichi} "
                f"| 手牌: {tiles_to_str(sort_tiles(gs.hand))} "
                f"| {shanten_info} | 剩{gs.tiles_left}枚"
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
            self._discard_event.clear()
            # 注意：吃/碰后 Mortal 的 _last_reaction 已经是 dahai（要打的牌）
            # 不能 clear，否则 decide_discard 找不到决策会 fallback

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
                target = hi.seat if hi.zimo else getattr(gs, 'last_discard_seat', gs.seat)
                self.ai.send_hora(hi.seat, target, hi.hu_tile)
            self.ai.send_end_kyoku()

        # 和牌表情已在 action 决策时发送（先表情后点和牌），此处不重复

        # 等一下再确认进入下一局
        delay = self.human.get_new_round_delay()
        await asyncio.sleep(delay)
        try:
            await self.client.confirm_new_round()
        except Exception as e:
            logger.debug(f"confirm_new_round: {e}")

    async def _send_win_emoji(self) -> None:
        """和牌时发送表情庆祝"""
        await self._send_emoji_for("win")

    async def _send_emoji_for(self, trigger: str) -> None:
        """通用表情发送，trigger: 'win' | 'riichi'"""
        import random
        emoji_cfg = getattr(self.config, 'emoji', None)
        if emoji_cfg is None or not getattr(emoji_cfg, 'enabled', True):
            return

        if trigger == "win":
            if not getattr(emoji_cfg, 'on_win', True):
                return
            pool = getattr(emoji_cfg, 'win_emojis', [2, 6, 7])
        elif trigger == "riichi":
            if not getattr(emoji_cfg, 'on_riichi', True):
                return
            pool = getattr(emoji_cfg, 'riichi_emojis', [3, 8])
        else:
            return

        if not pool:
            return

        emo_id = random.choice(pool)
        try:
            # 真人发表情：看到结果 → 反应 → 找到表情 → 点击
            await asyncio.sleep(random.uniform(0.3, 1.2))
            await self.client.send_emoji(emo_id)
        except Exception as e:
            logger.debug(f"发送{trigger}表情失败: {e}")

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
        """对局结束 — 使用 _action_lock 防止和 action handler 并发"""
        async with self._action_lock:
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

    def _find_operation_index(self, operation: dict, action_type: int,
                             combination: list[str]) -> int:
        """从 operation_list 中查找 AI 选择的 combination 对应的 index。
        
        服务端 operation_list 里同一个 type 可能有多个 combination 选项，
        例如吃 5m 时：["3m|4m", "4m|6m", "6m|7m"] 表示三种吃法。
        每个 pipe 分隔字符串对应 index 0, 1, 2。
        
        AI 返回的 combination 格式:
        - Mortal: 独立牌列表 ["4m", "6m"] (mjai 转换后的 ms 牌名)
        - ShantenAI: 原始服务端格式 ["3m|4m", "4m|6m"] (总是选第一个)
        
        Args:
            operation: pending_operation dict (含 operation_list)
            action_type: 操作类型 (2=吃, 3=碰, 4=暗杠, 5=明杠, 6=加杠)
            combination: AI 选择的牌组合
            
        Returns:
            匹配到的 index，未找到时返回 0
        """
        op_list = operation.get("operation_list", [])
        if not combination:
            return 0
        
        # 检测 AI combination 格式：含 "|" 说明是服务端原始格式 (ShantenAI)
        # 这种情况总是选第一个，index=0
        if any("|" in c for c in combination):
            logger.debug(f"combination 是服务端原始格式，index=0")
            return 0
        
        from tiles import normalize_aka
        
        # AI combination 是独立牌列表 (Mortal 格式)
        # 归一化后排序比较
        ai_tiles = sorted(normalize_aka(t) for t in combination)
        
        # 遍历 operation_list 中同 type 的 combination 选项
        same_type_idx = 0
        for op in op_list:
            if op.get("type") != action_type:
                continue
            for combo_str in op.get("combination", []):
                # 解析 "3m|4m" → ["3m", "4m"]
                parts = [p for p in combo_str.split("|") if len(p) >= 2 and p[-1] in "mpsz"]
                server_tiles = sorted(normalize_aka(t) for t in parts)
                if server_tiles == ai_tiles:
                    logger.info(
                        f"✅ 匹配 combination index={same_type_idx}: "
                        f"AI={combination} ↔ server={combo_str}"
                    )
                    return same_type_idx
                same_type_idx += 1
        
        # 归一化未匹配到 — 尝试精确匹配（含赤牌区分）
        same_type_idx = 0
        ai_tiles_exact = sorted(combination)
        for op in op_list:
            if op.get("type") != action_type:
                continue
            for combo_str in op.get("combination", []):
                parts = [p for p in combo_str.split("|") if len(p) >= 2 and p[-1] in "mpsz"]
                if sorted(parts) == ai_tiles_exact:
                    logger.info(
                        f"✅ 精确匹配 combination index={same_type_idx}: "
                        f"AI={combination} ↔ server={combo_str}"
                    )
                    return same_type_idx
                same_type_idx += 1
        
        logger.warning(
            f"⚠️ 未匹配到 combination! type={action_type} AI={combination} "
            f"server_options="
            f"{[op.get('combination') for op in op_list if op.get('type') == action_type]} "
            f"→ fallback index=0"
        )
        return 0

    async def _process_pending_operation(self) -> None:
        """处理待执行的操作"""
        gs = self.game_state
        if not gs or not gs.pending_operation:
            return

        operation = gs.pending_operation
        gs.pending_operation = None

        action = self.ai.decide_action(gs, operation)

        if action is None:
            # 跳过操作
            op_list = operation.get("operation_list", [])
            has_discard = any(op.get("type") == 1 for op in op_list)

            if has_discard:
                # 有出牌选项时直接出牌，不发 cancel_operation
                # 出牌(type=1)本身就隐含了放弃暗杠/自摸等其他选项
                # 发 cancel 会导致服务端立即摸切，没有出牌机会
                await self._do_discard()
            else:
                # 纯跳过别人的牌（不吃碰杠荣）
                display.show_action_decision("skip")
                await asyncio.sleep(self.human.get_skip_delay())
                await self.client.skip_action()
            return

        action_type = action.get("type", 0)
        self.human.on_action()

        if action_type in [8, 9]:
            # 自摸/荣和
            call = "tsumo" if action_type == 8 else "ron"
            display.show_action_decision(call)
            # 先发表情庆祝，再点和牌（真人：看到能和 → 兴奋发表情 → 点确认）
            await self._send_emoji_for("win")
            delay = self.human.get_call_delay(call)
            await asyncio.sleep(delay)
            await self.client.win(action_type)
        elif action_type == 7:
            # 立直
            display.show_action_decision("riichi")
            # 先发表情再立直（真人：决定立直 → 发个表情 → 宣言）
            await self._send_emoji_for("riichi")
            delay = self.human.get_riichi_delay()
            await asyncio.sleep(delay)
            tile = action.get("tile", "")
            if not tile:
                tile = self.ai.decide_discard(gs)
            is_moqie = (tile == gs.draw)
            await self.client.discard_tile(tile, is_riichi=True, moqie=is_moqie)
            # 等待服务端确认
            try:
                await asyncio.wait_for(self._discard_event.wait(), timeout=8)
            except asyncio.TimeoutError:
                logger.warning("⏰ 立直出牌确认超时 (8s)")
        elif action_type in [2, 3, 4, 5, 6]:
            # 吃(2)/碰(3)/暗杠(4)/明杠(5)/加杠(6)
            call = {2: "chi", 3: "pon", 4: "kan", 5: "kan", 6: "kan"}.get(action_type, "?")
            combination = action.get("combination", [])
            index = self._find_operation_index(operation, action_type, combination)
            display.show_action_decision(call, display.format_tiles(combination))
            delay = self.human.get_call_delay(call)
            await asyncio.sleep(delay)
            await self.client.chi_peng_gang(action_type, combination, index)
        else:
            logger.warning(f"未知操作类型: {action_type}")
            await asyncio.sleep(self.human.get_skip_delay())
            await self.client.skip_action()

    def _should_nerf(self) -> bool:
        """判断当前是否应该装弱（第一名且自己出牌次数在前 N 步内）"""
        if self._nerf_turns <= 0:
            return False
        gs = self.game_state
        if not gs:
            return False
        return gs.my_rank == 1 and gs.my_discard_count < self._nerf_turns

    def _nerf_sample_tile(self, reaction: dict, temperature: float = 2.0,
                           exclude_best: bool = False) -> str | None:
        """从 Mortal 的 q_values 做 softmax 采样选择次优牌。
        
        Args:
            temperature: softmax 温度，越高越随机
            exclude_best: 如果为 True，排除最优牌后再采样（保证换牌）
        """
        import math
        import random as _rng
        from ai.mortal import mjai_to_ms
        
        meta = reaction.get("meta", {})
        q_values = meta.get("q_values")
        mask_bits = meta.get("mask_bits")
        
        if not q_values or mask_bits is None:
            return None
        
        # 解码 mask_bits → 合法动作 index
        indices = []
        for i in range(46):
            if mask_bits & (1 << i):
                indices.append(i)
        
        if len(indices) != len(q_values):
            return None
        
        # 只取出牌动作 (index 0-36)，不随机化立直/吃碰杠等
        discard_indices = []
        discard_qs = []
        for i, idx in enumerate(indices):
            if idx < 37:  # 出牌动作
                discard_indices.append(i)
                discard_qs.append(q_values[i])
        
        if len(discard_qs) < 2:
            return None  # 只有一张可出，没法随机

        # 排除最优牌模式：去掉 Q 值最高的那张
        if exclude_best and len(discard_qs) >= 3:
            best_idx = discard_qs.index(max(discard_qs))
            discard_indices = [d for j, d in enumerate(discard_indices) if j != best_idx]
            discard_qs = [q for j, q in enumerate(discard_qs) if j != best_idx]
        
        # softmax with temperature
        max_q = max(discard_qs)
        exps = [math.exp((q - max_q) / temperature) for q in discard_qs]
        total = sum(exps)
        probs = [e / total for e in exps]
        
        # 带权随机采样
        r = _rng.random()
        cumsum = 0.0
        chosen_local = 0
        for j, p in enumerate(probs):
            cumsum += p
            if r <= cumsum:
                chosen_local = j
                break
        
        chosen_action_idx = indices[discard_indices[chosen_local]]
        
        # action index → mjai tile
        _IDX_TO_MJAI = [
            '1m','2m','3m','4m','5m','6m','7m','8m','9m',
            '1p','2p','3p','4p','5p','6p','7p','8p','9p',
            '1s','2s','3s','4s','5s','6s','7s','8s','9s',
            'E','S','W','N','P','F','C','0m','0p','0s',
        ]
        if chosen_action_idx >= len(_IDX_TO_MJAI):
            return None
        
        mjai_tile = _IDX_TO_MJAI[chosen_action_idx]
        ms_tile = mjai_to_ms(mjai_tile)
        
        # 打印采样结果
        best_idx = discard_qs.index(max(discard_qs))
        best_action = indices[discard_indices[best_idx]]
        best_tile = _IDX_TO_MJAI[best_action] if best_action < len(_IDX_TO_MJAI) else '?'
        chosen_q = discard_qs[chosen_local]
        best_q = discard_qs[best_idx]
        if chosen_local != best_idx:
            logger.info(
                f"🤡 装弱采样: {ms_tile}(q={chosen_q:.2f}) 替代最优 "
                f"{mjai_to_ms(best_tile)}(q={best_q:.2f}) [T={temperature}]"
            )
        
        return ms_tile

    async def _do_discard(self) -> None:
        """执行出牌"""
        gs = self.game_state
        if not gs:
            return

        # 检查服务端是否已经替我们出牌（超时自动摸切）
        if self._discard_confirmed:
            logger.info("服务端已确认出牌，跳过本次出牌")
            return

        # 装弱判断：第一名时前 N 手用 q_values 带权随机采样
        if self._should_nerf() and self._is_mortal:
            tile = self.ai.decide_discard(gs)
            reaction = getattr(self.ai, '_last_reaction', None) or {}
            sampled = self._nerf_sample_tile(reaction)
            if sampled:
                tile = sampled
            self._nerf_active = True
            logger.info(f"🤡 装弱中 (rank=1, 第{gs.my_discard_count+1}/{self._nerf_turns}手)")
        # 全局扰动：每手牌有 noise_rate 概率打次优牌（降低 AI 重合度）
        elif self._noise_rate > 0 and self._is_mortal and random.random() < self._noise_rate:
            tile = self.ai.decide_discard(gs)
            reaction = getattr(self.ai, '_last_reaction', None) or {}
            meta = reaction.get("meta") or {}
            q_values = meta.get("q_values")
            mask_bits = meta.get("mask_bits")
            logger.info(f"🎲 扰动触发: reaction_type={reaction.get('type')}, "
                         f"has_meta={bool(meta)}, has_q={q_values is not None}, "
                         f"has_mask={mask_bits is not None}")
            sampled = self._nerf_sample_tile(reaction, temperature=self._noise_temperature,
                                               exclude_best=True)
            if sampled and sampled != tile:
                tile = sampled
                self._nerf_active = True
                logger.info(f"🎲 扰动出牌 (rate={self._noise_rate}, T={self._noise_temperature})")
            else:
                logger.info(f"🎲 扰动触发但未生效: sampled={sampled}, original={tile}")
        else:
            tile = self.ai.decide_discard(gs)
        is_moqie = (tile == gs.draw)

        # 验证出牌合法性
        full_hand = gs.get_full_hand()
        if tile not in full_hand:
            # 赤宝牌映射：Mortal 可能返回 "5m" 但手牌只有 "0m"，反之亦然
            aka_map = {"5m": "0m", "5p": "0p", "5s": "0s",
                       "0m": "5m", "0p": "5p", "0s": "5s"}
            alt = aka_map.get(tile)
            if alt and alt in full_hand:
                logger.info(f"赤牌映射: {tile} → {alt}")
                tile = alt
                is_moqie = (tile == gs.draw)
            else:
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

        # 等待服务端确认出牌，防止迟到的 RPC 被当作下一巡操作（错位出牌）
        try:
            await asyncio.wait_for(self._discard_event.wait(), timeout=8)
        except asyncio.TimeoutError:
            logger.warning("⏰ 出牌确认超时 (8s)，可能被服务端自动摸切")


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
        bot._shutdown_event.set()
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
