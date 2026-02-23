"""雀魂 WebSocket 客户端 — 连接、登录、匹配、对局操作"""
import asyncio
import hashlib
import hmac
import logging
import random
import uuid

import aiohttp

from ms.base import MSRPCChannel
from ms.rpc import Lobby, FastTest
import ms.protocol_pb2 as pb

logger = logging.getLogger("majsoul.client")

MS_HOST = "https://game.maj-soul.com"


class MajsoulClient:
    """雀魂客户端，封装连接/登录/匹配/对局操作"""

    def __init__(self):
        self.channel: MSRPCChannel | None = None
        self.lobby: Lobby | None = None
        self.fast_test: FastTest | None = None
        self.account_id: int = 0
        self.nickname: str = ""
        self.version: str = ""
        self._game_channel: MSRPCChannel | None = None
        self._event_handlers: dict[str, list] = {}

    # ─── 连接 ─────────────────────────────────────

    async def connect(self) -> None:
        """连接到雀魂服务器"""
        async with aiohttp.ClientSession() as session:
            # 获取版本信息
            async with session.get(f"{MS_HOST}/1/version.json") as res:
                version_info = await res.json()
                self.version = version_info["version"]
                version_clean = self.version.replace(".w", "")
                logger.info(f"版本: {self.version}")

            # 获取服务器配置
            async with session.get(
                f"{MS_HOST}/1/v{self.version}/config.json"
            ) as res:
                config = await res.json()
                gateways = config["ip"][0]["gateways"]
                logger.info(f"路由网关数: {len(gateways)}")

            # 通过路由 API 获取可用节点
            gateway = random.choice(gateways)
            gateway_url = gateway["url"]
            async with session.get(
                f"{gateway_url}/api/clientgate/routes"
                f"?platform=Web&version={version_clean}"
            ) as res:
                route_data = await res.json()
                routes = route_data["data"]["routes"]
                # 选一个空闲的路由
                idle_routes = [r for r in routes if r["state"] == "idle"]
                if not idle_routes:
                    idle_routes = routes
                route = random.choice(idle_routes)
                domain = route["domain"]
                ssl = route.get("ssl", True)
                scheme = "wss" if ssl else "ws"
                endpoint = f"{scheme}://{domain}/"

        logger.info(f"连接网关: {endpoint}")
        self.channel = MSRPCChannel(endpoint)
        self.lobby = Lobby(self.channel)
        self.fast_test = FastTest(self.channel)

        await self.channel.connect(MS_HOST)
        logger.info("连接成功")

    async def close(self) -> None:
        """关闭连接"""
        if self.channel:
            await self.channel.close()
            logger.info("连接已关闭")
        if self._game_channel:
            await self._game_channel.close()

    # ─── 登录 ─────────────────────────────────────

    async def login(self, username: str, password: str) -> bool:
        """登录 CN 服（账号密码）"""
        logger.info(f"登录中: {username}")

        version_clean = self.version.replace(".w", "")
        uuid_key = str(uuid.uuid1())

        req = pb.ReqLogin()
        req.account = username
        req.password = hmac.new(
            b"lailai", password.encode(), hashlib.sha256
        ).hexdigest()
        req.device.is_browser = True
        req.random_key = uuid_key
        req.gen_access_token = True
        req.client_version_string = f"web-{version_clean}"
        req.currency_platforms.append(2)

        res = await self.lobby.login(req)

        if not res.access_token:
            logger.error(f"登录失败: {res}")
            return False

        self.account_id = res.account_id
        self.nickname = res.account.nickname if res.account else ""
        logger.info(f"登录成功! ID: {self.account_id}, 昵称: {self.nickname}")
        return True

    # ─── 心跳 ─────────────────────────────────────

    async def heartbeat_loop(self, interval: int = 30) -> None:
        """心跳保活循环"""
        while True:
            try:
                req = pb.ReqHeatBeat()
                req.no_operation_counter = 0
                await self.lobby.heatbeat(req)
                logger.debug("心跳 OK")
            except Exception as e:
                logger.warning(f"心跳失败: {e}")
            await asyncio.sleep(interval)

    # ─── 匹配 ─────────────────────────────────────

    # 段位场对局模式 ID
    MATCH_MODES = {
        # 四麻
        "4e_copper": 1,    # 铜之间四人东
        "4e_silver": 2,    # 银之间四人东
        "4e_gold": 3,      # 金之间四人东
        "4s_copper": 4,    # 铜之间四人南
        "4s_silver": 5,    # 银之间四人南
        "4s_gold": 6,      # 金之间四人南
        # 三麻
        "3e_copper": 11,
        "3e_silver": 12,
        "3e_gold": 13,
        "3s_copper": 14,
        "3s_silver": 15,
        "3s_gold": 16,
    }

    async def match(self, room_type: str = "4e",
                    level: str = "copper") -> bool:
        """开始段位赛匹配"""
        mode_key = f"{room_type}_{level}"
        mode_id = self.MATCH_MODES.get(mode_key)
        if mode_id is None:
            logger.error(f"未知的匹配模式: {mode_key}")
            logger.error(f"可用模式: {list(self.MATCH_MODES.keys())}")
            return False

        logger.info(f"开始匹配: {mode_key} (mode_id={mode_id})")

        req = pb.ReqJoinMatchQueue()
        req.match_mode = mode_id
        res = await self.lobby.match_game(req)

        if res.error and res.error.code:
            logger.error(f"匹配失败: code={res.error.code}")
            return False

        logger.info("匹配请求已发送，等待对手...")
        return True

    async def cancel_match(self) -> bool:
        """取消匹配"""
        req = pb.ReqCancelMatchQueue()
        res = await self.lobby.cancel_match(req)
        logger.info("已取消匹配")
        return True

    # ─── 事件注册 ──────────────────────────────────

    def on(self, event_name: str, handler):
        """注册事件处理器"""
        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = []
        self._event_handlers[event_name].append(handler)

    def _register_hooks(self) -> None:
        """注册 protobuf 消息钩子"""
        # 匹配成功通知
        self.channel.add_hook(
            ".lq.NotifyMatchGameStart", self._on_match_game_start
        )
        # 对局中的通知
        notifications = [
            (".lq.ActionNewRound", self._on_action),
            (".lq.ActionDealTile", self._on_action),
            (".lq.ActionDiscardTile", self._on_action),
            (".lq.ActionChiPengGang", self._on_action),
            (".lq.ActionAnGangAddGang", self._on_action),
            (".lq.ActionBaBei", self._on_action),
            (".lq.ActionHule", self._on_action),
            (".lq.ActionLiuJu", self._on_action),
            (".lq.ActionNoTile", self._on_action),
            (".lq.NotifyGameEndResult", self._on_game_end),
            (".lq.NotifyGameTerminate", self._on_game_end),
        ]
        for name, handler in notifications:
            self.channel.add_hook(name, handler)

    async def _on_match_game_start(self, data: bytes) -> None:
        """匹配成功"""
        msg = pb.NotifyMatchGameStart()
        msg.ParseFromString(data)

        logger.info("匹配成功! 连接对局服务器...")

        # 连接对局服务器
        connect_token = msg.connect_token
        game_url = msg.game_url
        # game_url 格式: "wss://xxx/gateway"

        # 认证对局
        req = pb.ReqAuthGame()
        req.account_id = self.account_id
        req.token = connect_token
        req.game_uuid = msg.game_uuid
        version_clean = self.version.replace(".w", "")
        req.client_version_string = f"web-{version_clean}"

        res = await self.fast_test.auth_game(req)

        if res.error and res.error.code:
            logger.error(f"对局认证失败: {res.error}")
            return

        # 找到自己的座位
        seat = -1
        players = res.players if hasattr(res, 'players') else []
        for i, p in enumerate(players):
            if p.account_id == self.account_id:
                seat = i
                break

        logger.info(f"对局认证成功，座位: {seat}")

        # 通知事件处理器
        for handler in self._event_handlers.get("game_start", []):
            await handler(seat, res)

        # 进入对局
        enter_req = pb.ReqCommon()
        await self.fast_test.enter_game(enter_req)

    async def _on_action(self, data: bytes) -> None:
        """对局中的操作通知"""
        wrapper = self.channel.unwrap(data)
        for handler in self._event_handlers.get("action", []):
            await handler(wrapper.name, wrapper.data)

    async def _on_game_end(self, data: bytes) -> None:
        """对局结束"""
        for handler in self._event_handlers.get("game_end", []):
            await handler(data)

    # ─── 对局操作 ──────────────────────────────────

    async def discard_tile(self, tile: str, is_riichi: bool = False,
                           moqie: bool = False) -> None:
        """出牌"""
        req = pb.ReqSelfOperation()
        req.tile = tile
        req.moqie = moqie
        req.is_liqi = is_riichi
        # req.gap_type = 0

        logger.debug(f"发送出牌: {tile} (立直={is_riichi}, 摸切={moqie})")
        await self.fast_test.input_operation(req)

    async def chi_peng_gang(self, type_: int, tiles: list[str],
                             cancel: bool = False) -> None:
        """吃碰杠操作"""
        req = pb.ReqChiPengGang()
        if cancel:
            req.cancel_operation = True
        else:
            req.type = type_
            for t in tiles:
                req.index.append(int(t) if t.isdigit() else 0)
        await self.fast_test.input_chi_peng_gang(req)

    async def win(self, type_: int = 8) -> None:
        """和牌 (自摸=8, 荣和=9)"""
        req = pb.ReqSelfOperation()
        req.type = type_
        await self.fast_test.input_operation(req)

    async def skip_action(self) -> None:
        """跳过操作（不吃碰杠）"""
        req = pb.ReqChiPengGang()
        req.cancel_operation = True
        await self.fast_test.input_chi_peng_gang(req)

    async def confirm_new_round(self) -> None:
        """确认进入下一局"""
        req = pb.ReqCommon()
        await self.fast_test.confirm_new_round(req)

    # ─── 工具方法 ──────────────────────────────────

    async def start_event_loop(self) -> None:
        """注册钩子并开始接收事件"""
        self._register_hooks()
        logger.info("事件循环已启动")
