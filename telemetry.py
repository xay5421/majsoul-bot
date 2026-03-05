"""雀魂客户端 telemetry 模拟 — 阿里云 SLS HTTP 日志上报

真实客户端的行为日志（bi_trace / clickLogMap / lobbyCostTime）
走的是 HTTP → 阿里云 SLS，**不是** WebSocket RPC。

两套系统：
  WebSocket RPC → login, match, game, heartbeat, logReport
  HTTP → Aliyun SLS → bi_trace, clickLogMap, lobbyCostTime

SLS endpoint: cn-hongkong.log.aliyuncs.com
Project: majsoul-hk-client
API: /track?APIVersion=0.6.0

真实客户端的日志发送机制：
  event → local queue → batch send (5-20 events / 5-10s flush)
  不是每个事件立即 POST，而是缓冲后批量发送。
"""
import asyncio
import json
import logging
import random
import time

import aiohttp

logger = logging.getLogger("majsoul.telemetry")

SLS_ENDPOINT = "https://majsoul-hk-client.cn-hongkong.log.aliyuncs.com"
SLS_TRACK_PATH = "/track"
SLS_API_VERSION = "0.6.0"

# 批量发送参数（模拟真实客户端 logUp 机制）
FLUSH_INTERVAL_MIN = 5.0   # 最短 flush 间隔 (秒)
FLUSH_INTERVAL_MAX = 10.0  # 最长 flush 间隔 (秒)
BATCH_SIZE_MAX = 20        # 队列超过此大小立即 flush


class TelemetryReporter:
    """阿里云 SLS 行为日志上报器

    模拟真实客户端的 bi_trace / clickLogMap / lobbyCostTime 上报。
    事件先缓冲到本地队列，定时批量通过 HTTP POST 发送到 SLS endpoint。
    """

    def __init__(self, client_version: str = "", device_info: dict | None = None,
                 enabled: bool = True):
        """
        Args:
            client_version: 游戏版本号 (如 "0.11.210.w")
            device_info: 设备信息字典，传入后自动填充公共字段
            enabled: 是否启用 SLS 上报 (False 则所有上报静默跳过)
        """
        self._client_version = client_version.replace(".w", "")
        self._app_runtime_id = f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        self._session: aiohttp.ClientSession | None = None
        self._enabled = enabled
        self._log_success_count = 0
        self._log_failed_count = 0

        # 日志缓冲队列 (模拟真实客户端的 logUp buffer)
        self._buffer: list[dict] = []
        self._flush_task: asyncio.Task | None = None

        # 设备信息（公共字段）
        self._device_info = device_info or {}
        self._device_type = self._device_info.get("device_type", "pc")
        self._device_gpu_name = self._device_info.get(
            "device_gpu_name",
            "ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.5)"
        )
        self._os_version = self._device_info.get("os_version", "Windows 10")

    @property
    def app_runtime_id(self) -> str:
        return self._app_runtime_id

    @property
    def log_success_count(self) -> int:
        return self._log_success_count

    @property
    def log_failed_count(self) -> int:
        return self._log_failed_count

    def set_version(self, version: str) -> None:
        """登录后更新版本号"""
        self._client_version = version.replace(".w", "")

    def set_account(self, account_id: int) -> None:
        """登录后设置账号 ID（SLS 事件会带 account_id + session_id）"""
        self._account_id = account_id
        # session_id = login_time + random（真实客户端的隐藏字段）
        self._session_id = f"{int(time.time())}_{account_id}_{random.randint(1000, 9999)}"
        logger.debug(f"📊 telemetry session: account={account_id}, sid={self._session_id}")

    async def start(self) -> None:
        """创建 HTTP session 并启动 flush 循环"""
        if not self._enabled:
            logger.info("📊 SLS 遥测已禁用")
            return
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/133.0.0.0 Safari/537.36"
                    ),
                    "Content-Type": "application/json",
                    "Origin": "https://game.maj-soul.com",
                    "Referer": "https://game.maj-soul.com/",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )
            logger.info(f"📊 SLS 遥测已启动 (endpoint: {SLS_ENDPOINT}, "
                        f"runtime_id: {self._app_runtime_id})")
        # 启动定时 flush 循环
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(
                self._flush_loop(), name="telemetry_flush"
            )

    async def close(self) -> None:
        """flush 剩余日志并关闭"""
        # 停止 flush 循环
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
            self._flush_task = None
        # flush 剩余
        await self._flush_now()
        # 关闭 HTTP session
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _get_common_fields(self) -> dict:
        """获取 bi_trace 公共字段 (_get_trace_common_info)

        包含 session_id（login_time + random）— 真实客户端的隐藏字段，
        缺少这个 SLS 很容易识别模拟器。
        """
        fields = {
            "device_type": self._device_type,
            "client_version": self._client_version,
            "res_version": self._client_version,
            "device_gpu_name": self._device_gpu_name,
            "os_version": self._os_version,
            "app_runtime_id": self._app_runtime_id,
        }
        if hasattr(self, '_account_id'):
            fields["account_id"] = str(self._account_id)
        if hasattr(self, '_session_id'):
            fields["session_id"] = self._session_id
        return fields

    # ─── 缓冲 & 批量发送 ────────────────────────────

    def _enqueue(self, log_entry: dict) -> None:
        """将日志条目加入缓冲队列

        队列超过 BATCH_SIZE_MAX 时自动触发 flush。
        """
        if not self._enabled:
            return
        self._buffer.append(log_entry)
        # 队列满了立即 flush（fire-and-forget）
        if len(self._buffer) >= BATCH_SIZE_MAX:
            asyncio.ensure_future(self._flush_now())

    async def _flush_loop(self) -> None:
        """定时 flush 循环（模拟真实客户端 logUp 定时器）

        每 5-10 秒 flush 一次缓冲队列，间隔加随机抖动。
        """
        try:
            while True:
                interval = random.uniform(FLUSH_INTERVAL_MIN, FLUSH_INTERVAL_MAX)
                await asyncio.sleep(interval)
                await self._flush_now()
        except asyncio.CancelledError:
            pass

    async def _flush_now(self) -> None:
        """立即 flush 缓冲队列中的所有日志"""
        if not self._buffer or not self._session:
            return
        # 取出当前缓冲（原子交换）
        batch = self._buffer
        self._buffer = []
        await self._send_to_sls(batch)

    async def _send_to_sls(self, logs: list[dict]) -> bool:
        """发送日志到阿里云 SLS

        API: POST /track?APIVersion=0.6.0
        Header: x-log-bodyrawsize
        Body: JSON with __logs__ array

        Args:
            logs: 日志条目列表

        Returns:
            True if successful
        """
        if not self._enabled or not self._session or not logs:
            return False

        try:
            body = json.dumps({"__logs__": logs})
            body_bytes = body.encode("utf-8")

            url = f"{SLS_ENDPOINT}{SLS_TRACK_PATH}"
            params = {"APIVersion": SLS_API_VERSION}
            headers = {
                "x-log-bodyrawsize": str(len(body_bytes)),
            }

            async with self._session.post(
                url, data=body_bytes, params=params, headers=headers
            ) as resp:
                if resp.status == 200:
                    self._log_success_count += len(logs)
                    logger.debug(f"📊 SLS flush: {len(logs)} 条日志 → 200 OK")
                    return True
                else:
                    self._log_failed_count += len(logs)
                    logger.debug(f"📊 SLS flush 失败: {len(logs)} 条 → HTTP {resp.status}")
                    return False
        except Exception as e:
            self._log_failed_count += len(logs)
            logger.debug(f"📊 SLS flush 异常 (可忽略): {e}")
            return False

    # ─── 高级 API ─────────────────────────────────

    async def track_event(self, event: str, extra: dict | None = None) -> None:
        """上报 bi_trace 事件（缓冲，不立即发送）

        真实客户端: trackEvent → bi_trace → _get_trace_common_info → queue → logUp → SLS

        Args:
            event: 事件名 (如 "page_enter", "showEnter")
            extra: 事件特有字段
        """
        log_entry = self._get_common_fields()
        log_entry["event"] = event
        log_entry["trace_id"] = self._app_runtime_id
        log_entry["timestamp"] = str(int(time.time() * 1000))
        if extra:
            log_entry.update(extra)
        logger.debug(f"📊 bi_trace: {event}")
        self._enqueue(log_entry)

    async def report_click_count(self, click_log_map: dict[str, int],
                                  page: str = "lobby") -> None:
        """上报 clickLogMap 汇总（缓冲，不立即发送）

        真实客户端: startClickLog → clickLogMap[event]++ →
                   reportClickCount → sendHistory → queue → logUp → SLS

        Args:
            click_log_map: 事件计数 (如 {"OPEN_MATCH_UI": 2, "CLOSE_MATCH_UI": 2})
            page: 当前页面
        """
        log_entry = self._get_common_fields()
        log_entry["event"] = "reportClickCount"
        log_entry["clickLogMap"] = json.dumps(click_log_map)
        log_entry["page"] = page
        log_entry["timestamp"] = str(int(time.time() * 1000))
        log_entry["trace_id"] = self._app_runtime_id
        logger.debug(f"📊 clickLogMap: {click_log_map}")
        self._enqueue(log_entry)

    async def report_lobby_cost_time(self, page: str, cost_time: int) -> None:
        """上报 lobbyCostTime（缓冲，不立即发送）

        Args:
            page: 页面名 (如 "lobby")
            cost_time: 停留时长 (毫秒)
        """
        log_entry = self._get_common_fields()
        log_entry["event"] = "lobbyCostTime"
        log_entry["page"] = page
        log_entry["cost_time"] = str(cost_time)
        log_entry["timestamp"] = str(int(time.time() * 1000))
        log_entry["trace_id"] = self._app_runtime_id
        logger.debug(f"📊 lobbyCostTime: {page} {cost_time}ms")
        self._enqueue(log_entry)

    async def report_show_enter(self) -> None:
        """登录后上报 showEnter"""
        await self.track_event("showEnter")

    async def report_first_in(self) -> None:
        """首次进入大厅"""
        await self.track_event("firstIn")

    async def report_page_enter(self, page: str = "lobby") -> None:
        """页面进入"""
        await self.track_event("page_enter", {"page": page})

    async def report_page_visit(self, page: str) -> None:
        """页面浏览"""
        await self.track_event("show_page_visit", {"page": page})

    async def report_match_ui(self, action: str = "OPEN_MATCH_UI") -> None:
        """匹配 UI 事件 (OPEN_MATCH_UI / CLOSE_MATCH_UI)"""
        await self.track_event(action)
