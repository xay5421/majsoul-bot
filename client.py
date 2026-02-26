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
from codec import decode as xor_decode


class MatchError1023(Exception):
    """匹配失败：账号仍在对局中 (error code 1023)"""
    pass

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
        self._username: str = ""
        self._password: str = ""
        self._last_connect_token: str = ""
        self._last_game_uuid: str = ""
        self._game_disconnected = asyncio.Event()  # game server 断线信号
        self._in_game: bool = False  # 是否在对局中
        self._connect_lock = asyncio.Lock()  # 防止并发连接 game server

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

            # 通过路由 API 获取可用节点（带重试）
            route_data = None
            for _attempt in range(3):
                gateway = random.choice(gateways)
                gateway_url = gateway["url"]
                try:
                    async with session.get(
                        f"{gateway_url}/api/clientgate/routes"
                        f"?platform=Web&version={version_clean}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as res:
                        route_data = await res.json()
                        break
                except Exception as e:
                    logger.warning(f"路由 {gateway_url} 失败: {e}")
                    await asyncio.sleep(2)
            
            if not route_data:
                raise ConnectionError("所有路由节点不可用")
            
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
        """关闭连接（先 logout 再断开，避免 1003）"""
        if self._game_channel:
            try:
                await self._game_channel.close()
            except Exception:
                pass
            self._game_channel = None

        if self.channel:
            try:
                if self.lobby and self.access_token:
                    await self.lobby.logout(pb.ReqLogout())
                    logger.info("已登出")
            except Exception as e:
                logger.debug(f"登出异常 (可忽略): {e}")
            try:
                await self.channel.close()
            except Exception:
                pass
            logger.info("连接已关闭")

    async def reconnect_lobby(self) -> bool:
        """重新连接 lobby（断线后恢复）
        
        关闭旧 lobby 连接，重新 connect + login，保留 game channel。
        Returns:
            True 如果重连成功
        """
        logger.info("🔄 重连 lobby...")
        # 只关闭 lobby，保留 game channel
        old_game_channel = self._game_channel
        old_fast_test = self.fast_test
        
        if self.channel:
            try:
                await self.channel.close()
            except Exception:
                pass
        
        try:
            await self.connect()
            ok = await self.login(self._username, self._password)
            if ok:
                self._register_hooks()
                # 恢复 game channel 引用
                if old_game_channel and old_game_channel.is_connected:
                    self._game_channel = old_game_channel
                    self.fast_test = old_fast_test
                logger.info("✅ lobby 重连成功")
                return True
            else:
                logger.error("lobby 重连登录失败")
                return False
        except Exception as e:
            logger.error(f"lobby 重连失败: {e}")
            return False

    # ─── 登录 ─────────────────────────────────────

    async def login(self, username: str, password: str) -> bool:
        """登录 CN 服（账号密码）

        Returns:
            True 表示登录成功。登录后会自动检查残留对局。
        """
        self._username = username
        self._password = password
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
        req.reconnect = True  # 允许踢掉旧连接

        res = await self.lobby.login(req)

        if not res.access_token:
            logger.error(f"登录失败: {res}")
            return False

        self.account_id = res.account_id
        self.access_token = res.access_token
        self.nickname = res.account.nickname if res.account else ""
        logger.info(f"登录成功! ID: {self.account_id}, 昵称: {self.nickname}")

        # 完成登录初始化（loginSuccess + fetchInfo）
        # 不调这些的话段位匹配会报 1307
        await self._post_login_init()

        # 检查残留对局
        if res.game_info and res.game_info.connect_token:
            logger.info(
                f"🔄 发现残留对局: {res.game_info.game_uuid[:30]}... "
                f"token={res.game_info.connect_token[:16]}..."
            )
            self._pending_reconnect = {
                "connect_token": res.game_info.connect_token,
                "game_uuid": res.game_info.game_uuid,
                "location": res.game_info.location,
            }
        else:
            self._pending_reconnect = None

        return True

    async def _post_login_init(self) -> None:
        """登录后初始化（模拟客户端行为）

        客户端登录后会调用 loginSuccess + loginBeat + fetchInfo 完成初始化。
        不做这些调用的话，段位匹配会失败 (error 1307)。
        """
        try:
            await self.lobby.login_success(pb.ReqCommon())
            logger.debug("loginSuccess OK")
        except Exception as e:
            logger.warning(f"loginSuccess 失败 (可忽略): {e}")

        try:
            lb_req = pb.ReqLoginBeat()
            lb_req.contract = ""
            await self.lobby.login_beat(lb_req)
            logger.debug("loginBeat OK")
        except Exception as e:
            logger.warning(f"loginBeat 失败 (可忽略): {e}")

        try:
            await self.lobby.fetch_info(pb.ReqCommon())
            logger.debug("fetchInfo OK")
        except Exception as e:
            logger.warning(f"fetchInfo 失败 (可忽略): {e}")

        logger.info("登录初始化完成")

    async def check_and_reconnect_game(self) -> bool:
        """检查并重连残留对局（带重试）

        策略:
        1. 直接用当前 token 重连
        2. 失败 → 重新查询 token（可能变了）→ 重连
        3. token 没变 & code=2 → 重新登录拿新 session → 重连
        4. 仍然 code=2 → 放弃，等对局自然结束

        Returns:
            True 如果成功重连了一局
        """
        connect_token = None
        game_uuid = None

        # 先看登录时是否带了 game_info
        if self._pending_reconnect:
            info = self._pending_reconnect
            self._pending_reconnect = None
            connect_token = info["connect_token"]
            game_uuid = info["game_uuid"]
            logger.info("尝试重连登录时的残留对局...")
        else:
            # 主动查询
            gi = await self.lobby.fetch_gaming_info(pb.ReqCommon())
            gd = MessageToDict(gi, preserving_proto_field_name=True)
            game_info = gd.get("game_info", {})
            if game_info.get("connect_token"):
                connect_token = game_info["connect_token"]
                game_uuid = game_info["game_uuid"]
                logger.info(f"🔄 发现残留对局: {game_uuid[:30]}... token={connect_token[:16]}...")

        if not connect_token:
            return False

        # === 第 1 步: 直接重连 ===
        logger.info("重连残留对局 (尝试 1: 直接重连)...")
        success = await self._reconnect_game(connect_token, game_uuid)
        if success:
            return True

        # === 第 2 步: 重新查询 token ===
        await asyncio.sleep(3)
        try:
            gi = await self.lobby.fetch_gaming_info(pb.ReqCommon())
            gd = MessageToDict(gi, preserving_proto_field_name=True)
            game_info = gd.get("game_info", {})
            if not game_info.get("connect_token"):
                logger.info("残留对局已消失，可能已结束")
                return False
            new_token = game_info["connect_token"]
            game_uuid = game_info["game_uuid"]
            token_changed = new_token != connect_token
            logger.info(
                f"重新查询 token={new_token[:16]}... "
                f"({'变了' if token_changed else '没变'})"
            )
            connect_token = new_token
        except Exception as e:
            logger.warning(f"查询残留对局失败: {e}")
            token_changed = False

        if token_changed:
            # token 变了，用新 token 再试
            logger.info("重连残留对局 (尝试 2: 新 token)...")
            success = await self._reconnect_game(connect_token, game_uuid)
            if success:
                return True

        # === 第 3 步: 重新登录刷新 session ===
        if self._username and self._password:
            logger.warning("🔄 重连失败，尝试重新登录刷新 session...")
            try:
                await self.close()
                await asyncio.sleep(3)
                await self.connect()
                ok = await self.login(self._username, self._password)
                if ok:
                    gi = await self.lobby.fetch_gaming_info(pb.ReqCommon())
                    gd = MessageToDict(gi, preserving_proto_field_name=True)
                    game_info = gd.get("game_info", {})
                    if game_info.get("connect_token"):
                        connect_token = game_info["connect_token"]
                        game_uuid = game_info["game_uuid"]
                        logger.info(f"重新登录后发现残留对局: {game_uuid[:30]}... token={connect_token[:16]}...")
                        success = await self._reconnect_game(connect_token, game_uuid)
                        if success:
                            return True
                        logger.error("重新登录后重连仍失败 — token 可能已永久失效")
                    else:
                        logger.info("重新登录后残留对局已消失")
                        return False
            except Exception as e:
                logger.error(f"重新登录失败: {e}")

        # === 放弃 ===
        logger.error("重连残留对局失败，等待对局自然结束")
        return False

    async def _reconnect_game(self, connect_token: str,
                               game_uuid: str) -> bool:
        """重连到残留对局并恢复状态"""
        try:
            await self._connect_game_server(
                game_url="", connect_token=connect_token,
                game_uuid=game_uuid
            )
            return True
        except Exception as e:
            err_msg = str(e)
            # authGame 认证失败不能容忍，必须重试
            if "authGame failed" in err_msg:
                logger.error(f"重连对局失败 (认证失败): {e}")
                return False
            # 其他错误：如果 game channel 连通且 fast_test 可用，视为部分成功
            if (self._game_channel and self._game_channel.is_connected
                    and self.fast_test):
                logger.warning(f"重连状态恢复不完整 (可继续): {e}")
                return True
            logger.error(f"重连对局失败: {e}")
            return False

    async def _on_game_server_disconnect(self) -> None:
        """game server WebSocket 断线回调"""
        logger.warning("⚠️ 对局服务器连接断开!")
        self._game_disconnected.set()

    async def auto_reconnect_game(self, max_retries: int = 3,
                                   retry_interval: int = 5) -> bool:
        """对局中途断线自动重连
        
        通过 lobby 查询残留对局信息，重新连接 game server。
        如果 lobby 也断了，先重连 lobby。
        
        Returns:
            True 如果重连成功
        """
        for attempt in range(1, max_retries + 1):
            logger.info(f"🔄 尝试重连对局 ({attempt}/{max_retries})...")
            try:
                # 先检查 lobby 是否还活着
                if not self.channel or not self.channel.is_connected:
                    logger.warning("lobby 也断了，先重连 lobby...")
                    if not await self.reconnect_lobby():
                        logger.error("lobby 重连失败")
                        if attempt < max_retries:
                            await asyncio.sleep(retry_interval)
                        continue

                # 通过 lobby 查残留对局
                gi = await self.lobby.fetch_gaming_info(pb.ReqCommon())
                gd = MessageToDict(gi, preserving_proto_field_name=True)
                game_info = gd.get("game_info", {})
                
                if not game_info.get("connect_token"):
                    logger.warning("未找到残留对局，可能对局已结束")
                    return False
                
                connect_token = game_info["connect_token"]
                game_uuid = game_info["game_uuid"]
                logger.info(f"找到残留对局: {game_uuid[:30]}... token={connect_token[:16]}...")
                
                # 重连
                success = await self._reconnect_game(connect_token, game_uuid)
                if success:
                    logger.info("✅ 对局重连成功!")
                    self._game_disconnected.clear()
                    return True
            except Exception as e:
                logger.error(f"重连尝试 {attempt} 失败: {e}")
            
            if attempt < max_retries:
                logger.info(f"等待 {retry_interval}s 后重试...")
                await asyncio.sleep(retry_interval)
        
        logger.error(f"重连失败，已尝试 {max_retries} 次")
        return False

    # ─── 心跳 ─────────────────────────────────────

    async def heartbeat_loop(self, interval: int = 10) -> None:
        """心跳保活循环 — 同时给 lobby 和 game server 发心跳
        
        interval 设为 10 秒，加速断线检测。
        禁用了 websockets 内建 ping，需要靠应用层心跳来检测连接存活。
        
        如果 lobby 心跳连续失败，会自动重连 lobby。
        """
        game_fail_count = 0
        lobby_fail_count = 0
        while True:
            # lobby 心跳
            try:
                req = pb.ReqHeatBeat()
                req.no_operation_counter = 0
                await asyncio.wait_for(self.lobby.heatbeat(req), timeout=8)
                logger.debug("心跳 OK (lobby)")
                lobby_fail_count = 0
            except asyncio.CancelledError:
                raise  # 让 task cancel 正常传播
            except Exception as e:
                lobby_fail_count += 1
                err_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"心跳失败 (lobby, 连续{lobby_fail_count}次): {err_detail}")
                if lobby_fail_count >= 5:
                    logger.error("lobby 心跳连续5次失败，尝试重连 lobby...")
                    reconnected = await self.reconnect_lobby()
                    if reconnected:
                        lobby_fail_count = 0
                    else:
                        logger.error("lobby 重连失败，退出心跳循环")
                        break
            
            # game server 心跳 (FastTest 用 checkNetworkDelay，不是 heartbeat)
            if self.fast_test and self._game_channel and self._game_channel.is_connected:
                try:
                    greq = pb.ReqCommon()
                    await asyncio.wait_for(
                        self.fast_test.check_network_delay(greq), timeout=8
                    )
                    logger.debug("心跳 OK (game)")
                    game_fail_count = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    game_fail_count += 1
                    err_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                    logger.warning(f"心跳失败 (game, 连续{game_fail_count}次): {err_detail}")
                    if game_fail_count >= 3:
                        logger.error("game server 心跳连续3次失败，标记断线")
                        self._game_disconnected.set()
                        game_fail_count = 0
            
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    # ─── 匹配 ─────────────────────────────────────

    # 段位场匹配模式表 (id → match_sid 的 type 值)
    # match_sid 格式: "{type}:{id}"
    # 从 lqc.lqbin (matchmode 配置表) 解析
    MATCH_MODES = {
        # id: (type, description)
        # 四麻 铜之间
        1:  (1, "铜之间四人东(免费)"),
        2:  (1, "铜之间四人东"),
        3:  (1, "铜之间四人南"),
        # 四麻 银之间
        4:  (1, "银之间四人东(免费)"),
        5:  (1, "银之间四人东"),
        6:  (1, "银之间四人南"),
        # 四麻 金之间
        7:  (1, "金之间四人东(免费)"),
        8:  (1, "金之间四人东"),
        9:  (1, "金之间四人南"),
        # 三麻 铜之间
        17: (1, "铜之间三人东"),
        18: (1, "铜之间三人南"),
    }

    # 简写别名 → mode_id
    MATCH_ALIASES = {
        "4e_copper":      2,   # 铜之间四人东 (默认)
        "4e_copper_free": 1,   # 铜之间四人东 (免费)
        "4s_copper":      3,   # 铜之间四人南
        "4e_silver":      5,
        "4s_silver":      6,
        "4e_gold":        8,
        "4s_gold":        9,
        "3e_copper":      17,
        "3s_copper":      18,
    }

    async def match(self, room_type: str = "4e",
                    level: str = "copper") -> bool:
        """开始段位赛匹配 (使用 startUnifiedMatch API)

        Args:
            room_type: "4e" (四人东), "4s" (四人南), "3e", "3s"
            level: "copper", "silver", "gold"

        Returns:
            True 表示匹配请求成功
        """
        mode_key = f"{room_type}_{level}"
        mode_id = self.MATCH_ALIASES.get(mode_key)
        if mode_id is None:
            logger.error(f"未知的匹配模式: {mode_key}")
            logger.error(f"可用模式: {list(self.MATCH_ALIASES.keys())}")
            return False

        mode_info = self.MATCH_MODES.get(mode_id)
        if not mode_info:
            logger.error(f"未知的 mode_id: {mode_id}")
            return False

        type_id, desc = mode_info
        match_sid = f"{type_id}:{mode_id}"
        self._current_match_sid = match_sid

        version_clean = self.version.replace(".w", "")
        logger.info(f"开始匹配: {desc} (match_sid={match_sid})")

        req = pb.ReqStartUnifiedMatch()
        req.match_sid = match_sid
        req.client_version_string = f"web-{version_clean}"
        res = await self.lobby.start_unified_match(req)

        if res.error and res.error.code:
            error_code = res.error.code
            logger.error(f"匹配失败: code={error_code}")
            logger.error(f"完整响应: {MessageToDict(res, preserving_proto_field_name=True)}")
            if error_code == 1023:
                raise MatchError1023("账号仍在对局中，无法匹配")
            return False

        logger.info("匹配请求已发送，等待对手...")
        return True

    async def cancel_match(self) -> bool:
        """取消匹配"""
        sid = getattr(self, '_current_match_sid', None)
        if sid:
            req = pb.ReqCancelUnifiedMatch()
            req.match_sid = sid
            await self.lobby.cancel_unified_match(req)
        else:
            # fallback: 旧 API
            req = pb.ReqCancelMatchQueue()
            await self.lobby.cancel_match(req)
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
        # 如果已经有活跃的 game channel（重连已在进行中），跳过
        if self._connect_lock.locked():
            logger.info("game server 连接正在进行中，跳过重复的匹配通知")
            return
        msg = pb.NotifyMatchGameStart()
        msg.ParseFromString(data)
        logger.info(
            f"匹配成功: uuid={msg.game_uuid[:30]}... "
            f"token={msg.connect_token[:16]}... game_url={msg.game_url}"
        )
        try:
            await self._connect_game_server(
                msg.game_url, msg.connect_token, msg.game_uuid
            )
        except Exception as e:
            logger.error(f"匹配成功后连接对局服务器失败: {e}")
            # 设置断线信号，让主循环走重连流程
            self._game_disconnected.set()

    async def _on_room_game_start(self, data: bytes) -> None:
        """友人房对局开始 — 连接对局服务器"""
        msg = pb.NotifyRoomGameStart()
        msg.ParseFromString(data)
        try:
            await self._connect_game_server(
                msg.game_url, msg.connect_token, msg.game_uuid
            )
        except Exception as e:
            logger.error(f"友人房连接对局服务器失败: {e}")
            self._game_disconnected.set()

    async def _connect_game_server(self, game_url: str, connect_token: str,
                                    game_uuid: str) -> None:
        """连接到对局服务器并认证
        
        game_url 是内网 IP (如 172.30.16.133:4027)，不能直接连。
        实际通过当前 lobby 的 route 域名 + /game-gateway 路径连接。
        
        支持断线重连：enterGame 会返回 GameRestore 数据。
        使用 _connect_lock 防止并发调用（匹配回调 + 重连可能同时触发）。
        """
        async with self._connect_lock:
            await self._connect_game_server_inner(game_url, connect_token, game_uuid)

    async def _connect_game_server_inner(self, game_url: str, connect_token: str,
                                          game_uuid: str) -> None:
        """_connect_game_server 的实际实现（在锁内调用）"""
        # 保存连接信息用于重连
        self._last_connect_token = connect_token
        self._last_game_uuid = game_uuid
        self._game_disconnected.clear()
        
        import re
        m = re.match(r'(wss://[^/]+)', self.channel._endpoint)
        route_base = m.group(1) if m else 'wss://route-5.maj-soul.com:443'
        ws_url = f'{route_base}/game-gateway'

        # 连接 + 认证，最多重试 3 次
        last_error = None
        for attempt in range(1, 4):
            if attempt > 1:
                logger.info(f"🔄 game server 连接重试 ({attempt}/3)...")
                await asyncio.sleep(2)
            
            logger.info(f"连接对局服务器: {ws_url} (game_url={game_url})")

            # 关闭旧的 game channel — 先注销断线回调，防止触发虚假的断线重连
            if self._game_channel:
                self._game_channel._on_disconnect_cb = None  # 禁止旧 channel 触发断线
                try:
                    await self._game_channel.close()
                except Exception:
                    pass

            # 创建新的 channel 连接到对局服务器
            self._game_channel = MSRPCChannel(ws_url)

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

            try:
                # 连接 — 注册断线回调 BEFORE connect，防止连上后瞬断漏掉
                self._game_channel.on_disconnect(self._on_game_server_disconnect)

                await self._game_channel.connect("https://game.maj-soul.com")
                logger.info("对局服务器连接成功")

                # 确认连接仍然存活（防止 connect 后瞬断）
                if not self._game_channel.is_connected:
                    logger.warning("对局服务器连接后立即断开")
                    last_error = ConnectionError("game server disconnected immediately")
                    continue

                # 在新 channel 上创建 FastTest 服务
                self.fast_test = FastTest(self._game_channel)

                # 认证对局 (需要 session=access_token)
                req = pb.ReqAuthGame()
                req.account_id = self.account_id
                req.token = connect_token
                req.game_uuid = game_uuid
                req.session = self.access_token

                logger.info(
                    f"authGame: token={connect_token[:16]}... "
                    f"session={self.access_token[:16]}... "
                    f"uuid={game_uuid[:30]}..."
                )
                res = await self.fast_test.auth_game(req)

                if res.error and res.error.code:
                    error_code = res.error.code
                    logger.error(
                        f"对局认证失败: code={error_code} "
                        f"token={connect_token[:16]}... "
                        f"session={self.access_token[:16]}..."
                    )
                    last_error = RuntimeError(f"authGame failed: code={error_code}")
                    # code=2: token 无效，同一个 token 重试没用，直接跳出
                    if error_code == 2:
                        break
                    continue  # 其他错误码可以重试

                # 认证成功，跳出重试循环
                self._game_disconnected.clear()
                break
            except Exception as e:
                logger.warning(f"game server 连接/认证失败: {type(e).__name__}: {e}")
                last_error = e
                continue
        else:
            # 3 次都失败
            raise last_error or RuntimeError("game server 连接失败")

        # 如果是 code=2 break 出来的，也要抛异常
        if last_error and "code=2" in str(last_error):
            raise last_error

        res_dict = MessageToDict(res, preserving_proto_field_name=True)

        # 找到自己的座位
        seat = -1
        seat_list = res_dict.get("seat_list", [])
        if seat_list:
            for i, aid in enumerate(seat_list):
                if aid == self.account_id:
                    seat = i
                    break
        else:
            players = res_dict.get("players", [])
            for i, p in enumerate(players):
                if p.get("account_id") == self.account_id:
                    seat = i
                    break

        logger.info(f"对局认证成功，座位: {seat}")

        # 通知事件处理器
        for handler in self._event_handlers.get("game_start", []):
            await handler(seat, res)

        # 进入对局 — 如果是重连，会返回 GameRestore
        enter_req = pb.ReqCommon()
        enter_res = await self.fast_test.enter_game(enter_req)

        if enter_res.is_end:
            logger.info("对局已结束")
            for handler in self._event_handlers.get("game_end", []):
                await handler(b"")
            return

        if enter_res.game_restore and enter_res.game_restore.actions:
            # 断线重连 — 重放 actions 恢复状态
            actions = enter_res.game_restore.actions
            logger.info(f"🔄 断线重连，重放 {len(actions)} 个动作恢复状态")
            for handler in self._event_handlers.get("game_restore", []):
                await handler(enter_res.game_restore)
        else:
            logger.info("已进入对局")

    async def _on_action_prototype(self, data: bytes) -> None:
        """对局中的 ActionPrototype 通知
        
        雀魂所有对局事件都包在 ActionPrototype 里：
        - ActionNewRound, ActionDealTile, ActionDiscardTile,
        - ActionChiPengGang, ActionAnGangAddGang,
        - ActionHule, ActionNoTile, ActionLiuJu 等
        
        data 字段经过 XOR 混淆，需要先解密。
        """
        msg = pb.ActionPrototype()
        msg.ParseFromString(data)

        action_name = msg.name
        action_data = xor_decode(msg.data)  # XOR 解密

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
        """出牌

        操作码: type=1 正常出牌, type=7 立直出牌
        """
        req = pb.ReqSelfOperation()
        req.type = 1  # 正常出牌
        req.tile = tile
        req.moqie = moqie
        # 立直: type=7
        if is_riichi:
            req.type = 7

        logger.info(f"发送出牌: {tile} (type={req.type}, 立直={is_riichi}, 摸切={moqie})")
        await self.fast_test.input_operation(req)

    async def skip_action(self) -> None:
        """跳过操作（不吃碰杠）"""
        req = pb.ReqSelfOperation()
        req.cancel_operation = True
        await self.fast_test.input_operation(req)

    async def win(self, action_type: int = 8) -> None:
        """和牌 (自摸=8, 荣和=9)"""
        req = pb.ReqSelfOperation()
        req.type = action_type
        logger.debug(f"发送和牌: type={action_type}")
        await self.fast_test.input_operation(req)

    async def chi_peng_gang(self, action_type: int, combination: list = None,
                            index: int = 0) -> None:
        """吃(2)/碰(3)/暗杠(4)/明杠(5)/加杠(6)
        
        ReqSelfOperation 没有 combination 字段。
        服务端根据 type + index 来确定操作。
        """
        req = pb.ReqSelfOperation()
        req.type = action_type
        req.index = index
        logger.info(f"发送副露: type={action_type} index={index}")
        await self.fast_test.input_operation(req)

    async def confirm_new_round(self) -> None:
        """确认进入下一局"""
        req = pb.ReqCommon()
        await self.fast_test.confirm_new_round(req)

    async def send_emoji(self, emo_id: int) -> None:
        """发送游戏内表情
        
        通过 broadcastInGame 发送表情。
        content 格式基于 GameUserInput 协议的 emo 字段。
        
        表情 ID 与角色相关：
        - 基础表情: 1-9 (每个角色都有)
        - 额外表情: 需要解锁 (Character.extra_emoji)
        """
        import json
        req = pb.ReqBroadcastInGame()
        # content 是 JSON 字符串，包含表情 ID
        req.content = json.dumps({"emo": emo_id})
        req.except_self = False
        try:
            await self.fast_test.broadcast_in_game(req)
            logger.info(f"发送表情: emo_id={emo_id}")
        except Exception as e:
            logger.warning(f"发送表情失败: {e}")

    # ─── 工具方法 ──────────────────────────────────

    async def start_event_loop(self) -> None:
        """注册钩子并开始接收事件"""
        self._register_hooks()
        logger.info("事件循环已启动")
