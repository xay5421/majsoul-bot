"""mjai AI 引擎接口

支持多种 AI 后端接入方式：
1. MjaiSubprocessAI — 子进程模式（stdin/stdout JSON，兼容 mjai.app 标准）
2. MjaiHTTPAI — HTTP 模式（兼容 Akagi/MajSoulAiBot 的 Flask 后端）
3. MjaiFileAI — 文件模式（调试用，读写 JSON 文件）

所有引擎实现 MjaiAI 基类，输入 mjai 事件列表，输出 mjai 动作。
"""
import asyncio
import json
import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

from ai.base import BaseAI
from game_state import GameState
from mjai_proto import (
    make_start_game, make_start_kyoku, make_tsumo, make_dahai,
    make_chi, make_pon, make_daiminkan, make_ankan, make_kakan,
    make_dora, make_reach, make_reach_accepted, make_hora,
    make_ryukyoku, make_end_kyoku, make_end_game,
    parse_mjai_action, mjai_to_majsoul, majsoul_to_mjai,
)

logger = logging.getLogger("majsoul.mjai_ai")


class MjaiAI(ABC):
    """mjai AI 引擎基类"""

    @abstractmethod
    async def react(self, events: list[dict]) -> dict | None:
        """
        输入一批 mjai 事件，返回 AI 的动作。

        Args:
            events: mjai 格式事件列表

        Returns:
            mjai 格式动作，None 表示无需动作
        """
        ...

    async def start(self) -> None:
        """启动引擎"""
        pass

    async def stop(self) -> None:
        """停止引擎"""
        pass


class MjaiSubprocessAI(MjaiAI):
    """子进程模式 AI — 兼容 mjai.app 标准

    启动一个子进程，通过 stdin/stdout 用 JSON line 通信。
    每次发送一行 JSON（事件列表），读取一行 JSON（动作）。
    """

    def __init__(self, command: list[str], player_id: int = 0):
        self.command = command
        self.player_id = player_id
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        cmd = self.command + [str(self.player_id)]
        logger.info(f"启动 AI 子进程: {' '.join(cmd)}")
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stop(self) -> None:
        if self._process:
            self._process.terminate()
            await self._process.wait()
            logger.info("AI 子进程已停止")

    async def react(self, events: list[dict]) -> dict | None:
        if not self._process or self._process.returncode is not None:
            logger.error("AI 子进程未运行")
            return None

        # 发送事件
        line = json.dumps(events, separators=(",", ":")) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        # 读取回复
        try:
            resp_line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.error("AI 子进程响应超时")
            return None

        if not resp_line:
            logger.error("AI 子进程无响应")
            return None

        try:
            return json.loads(resp_line.decode().strip())
        except json.JSONDecodeError as e:
            logger.error(f"AI 返回无效 JSON: {e}")
            return None


class MjaiHTTPAI(MjaiAI):
    """HTTP 模式 AI — 兼容 Akagi 的 Flask 后端

    通过 HTTP POST 发送 mjai 事件，接收 mjai 动作。
    """

    def __init__(self, url: str = "http://127.0.0.1:7331"):
        self.url = url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
        logger.info(f"HTTP AI 连接: {self.url}")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    async def react(self, events: list[dict]) -> dict | None:
        if not self._session:
            await self.start()

        try:
            async with self._session.post(
                f"{self.url}/react",
                json=events,
            ) as resp:
                if resp.status != 200:
                    logger.error(f"AI HTTP 错误: {resp.status}")
                    return None
                return await resp.json()
        except Exception as e:
            logger.error(f"AI HTTP 请求失败: {e}")
            return None


class MjaiMortalAI(MjaiAI):
    """Mortal 本地推理模式 — 直接加载模型权重"""

    def __init__(self, seat: int = 0):
        self._seat = seat
        self._bot = None

    async def start(self) -> None:
        from mortal_ai import check_mortal_available, load_mortal_bot
        ok, msg = check_mortal_available()
        if not ok:
            raise RuntimeError(f"Mortal 不可用: {msg}")
        self._bot = load_mortal_bot(self._seat)

    async def stop(self) -> None:
        self._bot = None

    async def react(self, events: list[dict]) -> dict | None:
        if not self._bot:
            return None

        # 检查 start_game 事件更新 seat
        for e in events:
            if e.get("type") == "start_game" and "id" in e:
                self._seat = e["id"]
                from mortal_ai import load_mortal_bot
                self._bot = load_mortal_bot(self._seat)

        return self._bot.react(events)


class MjaiAdapter(BaseAI):
    """将 mjai AI 引擎适配为 BaseAI 接口

    维护 mjai 事件历史，在每次决策时发送累积事件给 AI 引擎。
    """

    def __init__(self, engine: MjaiAI, player_id: int = 0):
        self.engine = engine
        self.player_id = player_id
        self._events: list[dict] = []        # 累积事件
        self._pending_events: list[dict] = [] # 待发送事件

    async def start(self) -> None:
        await self.engine.start()

    async def stop(self) -> None:
        await self.engine.stop()

    def on_game_start(self, state: GameState) -> None:
        """对局开始"""
        self.player_id = state.seat
        self._events = []
        self._pending_events = [make_start_game(self.player_id)]

    def on_round_start(self, state: GameState) -> None:
        """新一局开始"""
        # 构造四家手牌
        tehais = [["?"] * 13 for _ in range(state.player_count)]
        tehais[state.seat] = list(state.hand)
        # 如果庄家有14张，最后一张是摸牌
        if state.draw and state.seat == state.dealer:
            tehais[state.seat] = list(state.hand)  # 只放13张

        scores = [p.score for p in state.players]
        dora = state.dora_indicators[0] if state.dora_indicators else ""

        event = make_start_kyoku(
            round_wind=state.round_wind,
            round_num=state.round_num,
            honba=state.honba,
            riichi_sticks=state.riichi_sticks,
            dealer=state.dealer,
            scores=scores,
            dora_indicator=dora,
            tehais=tehais,
            player_id=self.player_id,
        )
        self._pending_events.append(event)

        # 庄家的第一次摸牌
        if state.draw and state.seat == state.dealer:
            self._pending_events.append(
                make_tsumo(state.seat, state.draw, is_self=True)
            )

    def add_event(self, event: dict) -> None:
        """添加 mjai 事件到待发送队列"""
        self._pending_events.append(event)
        self._events.append(event)

    async def get_action(self, state: GameState) -> dict | None:
        """获取 AI 的决策

        Returns:
            解析后的动作（majsoul 格式），None 表示跳过
        """
        if not self._pending_events:
            return None

        events = self._pending_events
        self._pending_events = []

        logger.debug(f"发送 {len(events)} 个事件给 AI")
        for e in events:
            logger.debug(f"  → {json.dumps(e, ensure_ascii=False)}")

        mjai_action = await self.engine.react(events)

        if mjai_action is None:
            return None

        logger.info(
            f"AI 决策: {json.dumps(mjai_action, ensure_ascii=False)}"
        )

        return parse_mjai_action(mjai_action)

    # ─── BaseAI 接口实现 ──────────────────────────────
    # 这些方法在 mjai 模式下不直接使用，
    # 而是通过 MjaiAdapter.get_action() 获取完整决策

    def decide_discard(self, state: GameState) -> str:
        """同步接口，mjai 模式下不应调用"""
        raise NotImplementedError(
            "MjaiAdapter 使用异步接口 get_action()，不支持同步调用"
        )

    def decide_action(self, state: GameState, actions: dict) -> dict | None:
        """同步接口，mjai 模式下不应调用"""
        raise NotImplementedError(
            "MjaiAdapter 使用异步接口 get_action()，不支持同步调用"
        )

    def on_game_end(self, result: dict) -> None:
        self._pending_events.append(make_end_game())
