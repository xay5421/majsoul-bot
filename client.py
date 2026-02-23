"""雀魂 WebSocket 客户端 — 连接、登录、匹配、对局操作"""
import asyncio
import hashlib
import hmac
import logging
import random
import uuid

import aiohttp
from google.protobuf.json_format import MessageToDict

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
        self.access_token: str = ""
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
        self.access_token = res.access_token
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
            logger.error(f"完整响应: {MessageToDict(res, preserving_proto_field_name=True)}")
            return False

        logger.info("匹配请求已发送，等待对手...")
        return True

    async def cancel_match(self) -> bool:
        """取消匹配"""
        req = pb.ReqCancelMatchQueue()
        res = await self.lobby.cancel_match(req)
        logger.info("已取消匹配")
        return True

    async def create_ai_room(self, room_type: str = "4e") -> int | None:
        """创建友人房 + AI 对手

        Args:
            room_type: "4e" (四人东), "4s" (四人南)

        Returns:
            room_id 或 None
        """
        player_count = 4 if room_type.startswith("4") else 3
        # mode: 1=四人南(完整), 2=四人东(东风)
        mode_map = {"4e": 2, "4s": 1, "3e": 12, "3s": 11}
        mode = mode_map.get(room_type, 2)

        req = pb.ReqCreateRoom()
        req.player_count = player_count
        req.mode.mode = mode
        req.mode.ai = True
        req.client_version_string = self.version

        # 标准规则
        dr = req.mode.detail_rule
        dr.time_fixed = 5
        dr.time_add = 20
        dr.dora_count = 3
        dr.shiduan = 1
        dr.init_point = 25000
        dr.fandian = 30000
        dr.can_jifei = True
        dr.have_liujumanguan = True
        dr.have_biao_dora = True
        dr.have_gang_biao_dora = True
        dr.have_li_dora = True
        dr.have_gang_li_dora = True
        dr.have_sifenglianda = True
        dr.have_sigangsanle = True
        dr.have_sijializhi = True
        dr.have_jiuzhongjiupai = True
        dr.have_sanjiahele = False
        dr.have_toutiao = True
        dr.have_helelianzhuang = True
        dr.have_helezhongju = True
        dr.have_tingpailianzhuang = True
        dr.have_tingpaizhongju = True

        res = await self.lobby.create_room(req)
        if res.error and res.error.code:
            logger.error(f"创建房间失败: code={res.error.code}")
            return None

        room = MessageToDict(res, preserving_proto_field_name=True).get("room", {})
        room_id = room.get("room_id", 0)
        logger.info(f"房间创建成功: {room_id}")

        # 加入 AI 玩家
        ai_count = player_count - 1
        for i in range(ai_count):
            add_req = pb.ReqAddRoomRobot()
            add_req.position = i + 1  # 位置 1, 2, 3
            add_res = await self.lobby.add_room_robot(add_req)
            if add_res.error and add_res.error.code:
                logger.error(f"添加 AI 失败 (pos={i+1}): code={add_res.error.code}")
            else:
                logger.info(f"AI 玩家 {i+1} 加入")

        return room_id

    async def start_room(self) -> bool:
        """开始友人房对局"""
        req = pb.ReqRoomStart()
        res = await self.lobby.start_room(req)
        if res.error and res.error.code:
            logger.error(f"开始对局失败: code={res.error.code}")
            return False
        logger.info("对局开始!")
        return True

    # ─── 事件注册 ──────────────────────────────────

    def on(self, event_name: str, handler):
        """注册事件处理器"""
        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = []
        self._event_handlers[event_name].append(handler)

    def _register_hooks(self) -> None:
        """注册 protobuf 消息钩子（lobby channel）"""
        # 匹配成功通知
        self.channel.add_hook(
            ".lq.NotifyMatchGameStart", self._on_match_game_start
        )
        # 友人房对局开始通知
        self.channel.add_hook(
            ".lq.NotifyRoomGameStart", self._on_room_game_start
        )

    async def _on_match_game_start(self, data: bytes) -> None:
        """匹配成功 — 连接对局服务器"""
        msg = pb.NotifyMatchGameStart()
        msg.ParseFromString(data)
        await self._connect_game_server(
            msg.game_url, msg.connect_token, msg.game_uuid
        )

    async def _on_room_game_start(self, data: bytes) -> None:
        """友人房对局开始 — 连接对局服务器"""
        msg = pb.NotifyRoomGameStart()
        msg.ParseFromString(data)
        await self._connect_game_server(
            msg.game_url, msg.connect_token, msg.game_uuid
        )

    async def _connect_game_server(self, game_url: str, connect_token: str,
                                    game_uuid: str) -> None:
        """连接到对局服务器并认证"""
        logger.info(f"连接对局服务器: {game_url}")

        # game_url 可能是 "wss://xxx" 或 "xxx:port" 格式
        if not game_url.startswith("wss://") and not game_url.startswith("ws://"):
            game_url = f"wss://{game_url}"

        # 提取 host 作为 origin
        from urllib.parse import urlparse
        parsed = urlparse(game_url)
        origin = f"https://{parsed.hostname}"

        # 创建新的 channel 连接到对局服务器
        self._game_channel = MSRPCChannel(game_url)

        # 注册对局事件 hook（在 connect 之前注册）
        self._game_channel.add_hook(
            ".lq.ActionPrototype", self._on_action_prototype
        )
        self._game_channel.add_hook(
            ".lq.NotifyGameEndResult", self._on_game_end
        )
        self._game_channel.add_hook(
            ".lq.NotifyGameTerminate", self._on_game_end
        )

        # 连接（会自动启动 dispatch_msg 循环）
        await self._game_channel.connect(origin)
        logger.info("对局服务器连接成功")

        # 在新 channel 上创建 FastTest 服务
        self.fast_test = FastTest(self._game_channel)

        # 认证对局
        req = pb.ReqAuthGame()
        req.account_id = self.account_id
        req.token = connect_token
        req.game_uuid = game_uuid
        version_clean = self.version.replace(".w", "")
        req.client_version_string = f"web-{version_clean}"

        res = await self.fast_test.auth_game(req)

        if res.error and res.error.code:
            logger.error(f"对局认证失败: code={res.error.code}")
            return

        res_dict = MessageToDict(res, preserving_proto_field_name=True)

        # 找到自己的座位
        seat = -1
        seat_list = res_dict.get("seat_list", [])
        if seat_list:
            # seat_list 包含各座位的 account_id
            for i, aid in enumerate(seat_list):
                if aid == self.account_id:
                    seat = i
                    break
        else:
            # fallback: players 列表
            players = res_dict.get("players", [])
            for i, p in enumerate(players):
                if p.get("account_id") == self.account_id:
                    seat = i
                    break

        logger.info(f"对局认证成功，座位: {seat}")

        # 通知事件处理器
        for handler in self._event_handlers.get("game_start", []):
            await handler(seat, res)

        # 进入对局
        enter_req = pb.ReqCommon()
        await self.fast_test.enter_game(enter_req)
        logger.info("已进入对局")

    async def _on_action_prototype(self, data: bytes) -> None:
        """对局中的 ActionPrototype 通知
        
        雀魂所有对局事件都包在 ActionPrototype 里：
        - ActionNewRound, ActionDealTile, ActionDiscardTile,
        - ActionChiPengGang, ActionAnGangAddGang,
        - ActionHule, ActionNoTile, ActionLiuJu 等
        """
        msg = pb.ActionPrototype()
        msg.ParseFromString(data)

        action_name = msg.name
        action_data = msg.data

        logger.debug(f"ActionPrototype: {action_name} ({len(action_data)} bytes)")

        for handler in self._event_handlers.get("action", []):
            await handler(action_name, action_data)

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
