"""测试段位场匹配 - 尝试 start_unified_match 和 match_game 两种 API"""
import asyncio
import hashlib
import hmac
import uuid
import logging
import random

import aiohttp
from google.protobuf.json_format import MessageToDict

from ms.base import MSRPCChannel
from ms.rpc import Lobby
import ms.protocol_pb2 as pb
from config import load_config

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger()

MS_HOST = "https://game.maj-soul.com"

# 从 lqc.lqbin 解析出的匹配模式表
# match_sid = f"{type}:{id}"
# type=1 标准段位, type=2 乱斗, type=3 休闲
MATCH_MODE_TABLE = {
    # id: (type, room_name, mode_desc, min_rank, max_rank)
    1:  (1, "铜之间", "四人东(免费)", 10101, 10203),
    2:  (1, "铜之间", "四人东", 10101, 10203),
    3:  (1, "铜之间", "四人南", 10101, 10203),
    4:  (1, "银之间", "四人东(免费)", 10201, 10303),
    5:  (1, "银之间", "四人东", 10201, 10303),
    6:  (1, "银之间", "四人南", 10201, 10303),
    7:  (1, "金之间", "四人东(免费)", 10301, 10403),
    8:  (1, "金之间", "四人东", 10301, 10403),
    9:  (1, "金之间", "四人南", 10301, 10403),
    10: (1, "玉之间", "四人东(免费)", 10401, 10503),
    11: (1, "玉之间", "四人东", 10401, 10503),
    12: (1, "玉之间", "四人南", 10401, 10503),
    13: (2, "乱斗之间", "四人东", 0, 0),
    14: (2, "乱斗之间", "四人南", 0, 0),
    15: (1, "王座间", "四人东", 10501, 10720),
    16: (1, "王座间", "四人南", 10501, 10720),
    17: (1, "铜之间", "三人东", 20101, 20203),
    18: (1, "铜之间", "三人南", 20101, 20203),
    19: (1, "银之间", "三人东", 20201, 20303),
    20: (1, "银之间", "三人南", 20201, 20303),
    21: (1, "金之间", "三人东", 20301, 20403),
    22: (1, "金之间", "三人南", 20301, 20403),
    23: (1, "玉之间", "三人东", 20401, 20503),
    24: (1, "玉之间", "三人南", 20401, 20503),
    25: (1, "王座间", "三人东", 20501, 20720),
    26: (1, "王座间", "三人南", 20501, 20720),
    29: (3, "休闲普通场", "四人东", 0, 0),
    30: (3, "休闲普通场", "四人南", 0, 0),
    31: (3, "休闲普通场", "三人东", 0, 0),
    32: (3, "休闲普通场", "三人南", 0, 0),
}


async def connect_and_login():
    """连接并登录"""
    cfg = load_config()
    
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{MS_HOST}/1/version.json") as res:
            v = await res.json()
            version = v["version"]
            version_clean = version.replace(".w", "")
            logger.info(f"Version: {version}")

        async with session.get(f"{MS_HOST}/1/v{version}/config.json") as res:
            config = await res.json()
            gateways = config["ip"][0]["gateways"]

        gateway = random.choice(gateways)
        gateway_url = gateway["url"]
        async with session.get(
            f"{gateway_url}/api/clientgate/routes?platform=Web&version={version_clean}"
        ) as res:
            route_data = await res.json()
            routes = route_data["data"]["routes"]
            idle_routes = [r for r in routes if r["state"] == "idle"]
            if not idle_routes:
                idle_routes = routes
            route = random.choice(idle_routes)
            domain = route["domain"]
            ssl = route.get("ssl", True)
            scheme = "wss" if ssl else "ws"
            endpoint = f"{scheme}://{domain}/"

    logger.info(f"Connecting to: {endpoint}")
    channel = MSRPCChannel(endpoint)
    lobby = Lobby(channel)
    await channel.connect(MS_HOST)

    # Login
    req = pb.ReqLogin()
    req.account = cfg.account.username
    req.password = hmac.new(b"lailai", cfg.account.password.encode(), hashlib.sha256).hexdigest()
    req.device.is_browser = True
    req.random_key = str(uuid.uuid1())
    req.gen_access_token = True
    req.client_version_string = f"web-{version_clean}"
    req.currency_platforms.append(2)
    
    res = await lobby.login(req)
    if not res.access_token:
        logger.error(f"Login failed: {MessageToDict(res)}")
        await channel.close()
        return None, None, None
    
    logger.info(f"Logged in: {res.account.nickname} (id={res.account_id})")
    logger.info(f"Level (四麻): {res.account.level.id} score={res.account.level.score}")
    logger.info(f"Level3 (三麻): {res.account.level3.id} score={res.account.level3.score}")
    
    return lobby, channel, version_clean


async def test_unified_match(lobby, version_clean):
    """测试 start_unified_match API"""
    logger.info("=" * 60)
    logger.info("测试 start_unified_match API")
    logger.info("=" * 60)
    
    # 我们的段位是 10103 (初心三星)
    # 铜之间要求 min_rank=10101, 我们满足
    # 测试标准段位场 (type=1)
    test_cases = [
        (1, 1, "铜之间四人东免费"),
        (1, 2, "铜之间四人东"),
        (1, 3, "铜之间四人南"),
        (1, 17, "铜之间三人东"),
        (1, 18, "铜之间三人南"),
        (3, 29, "休闲四人东"),
        (3, 30, "休闲四人南"),
    ]
    
    for type_id, mode_id, desc in test_cases:
        sid = f"{type_id}:{mode_id}"
        req = pb.ReqStartUnifiedMatch()
        req.match_sid = sid
        req.client_version_string = f"web-{version_clean}"
        
        try:
            res = await lobby.start_unified_match(req)
            result = MessageToDict(res, preserving_proto_field_name=True)
            err_code = result.get('error', {}).get('code', 0)
            
            if err_code == 0:
                logger.info(f"✅ '{sid}' ({desc}): SUCCESS! 进入匹配队列")
                # 立即取消
                cancel = pb.ReqCancelUnifiedMatch()
                cancel.match_sid = sid
                await lobby.cancel_unified_match(cancel)
                logger.info(f"   已取消匹配")
                return sid  # 找到了！
            elif err_code == 1302:
                logger.info(f"⚡ '{sid}' ({desc}): 已在队列中 (1302)")
                cancel = pb.ReqCancelUnifiedMatch()
                cancel.match_sid = sid
                await lobby.cancel_unified_match(cancel)
                return sid
            else:
                logger.warning(f"❌ '{sid}' ({desc}): error={err_code} {result}")
        except Exception as e:
            logger.error(f"💥 '{sid}' ({desc}): exception={e}")
        
        await asyncio.sleep(0.5)
    
    return None


async def test_match_game(lobby, version_clean):
    """测试 match_game (旧 API)"""
    logger.info("=" * 60)
    logger.info("测试 match_game (ReqJoinMatchQueue) API")
    logger.info("=" * 60)
    
    test_modes = [1, 2, 3, 17, 18, 29, 30]
    
    for mode_id in test_modes:
        info = MATCH_MODE_TABLE.get(mode_id, (0, "?", "?", 0, 0))
        desc = f"{info[1]}{info[2]}"
        
        req = pb.ReqJoinMatchQueue()
        req.match_mode = mode_id
        req.client_version_string = f"web-{version_clean}"
        
        try:
            res = await lobby.match_game(req)
            result = MessageToDict(res, preserving_proto_field_name=True)
            err_code = result.get('error', {}).get('code', 0)
            
            if err_code == 0:
                logger.info(f"✅ mode={mode_id} ({desc}): SUCCESS!")
                cancel = pb.ReqCancelMatchQueue()
                await lobby.cancel_match(cancel)
                logger.info(f"   已取消匹配")
                return mode_id
            elif err_code == 1302:
                logger.info(f"⚡ mode={mode_id} ({desc}): 已在队列 (1302)")
                cancel = pb.ReqCancelMatchQueue()
                await lobby.cancel_match(cancel)
                return mode_id
            else:
                logger.warning(f"❌ mode={mode_id} ({desc}): error={err_code}")
        except Exception as e:
            logger.error(f"💥 mode={mode_id} ({desc}): exception={e}")
        
        await asyncio.sleep(0.5)
    
    return None


async def test_fetch_current_match(lobby):
    """获取当前可用匹配信息"""
    logger.info("=" * 60)
    logger.info("获取当前匹配信息 (fetchCurrentMatchInfo)")
    logger.info("=" * 60)
    
    req = pb.ReqCurrentMatchInfo()
    res = await lobby.fetch_current_match_info(req)
    result = MessageToDict(res, preserving_proto_field_name=True)
    
    if 'error' in result and result['error'].get('code'):
        logger.error(f"获取匹配信息失败: {result}")
        return
    
    matches = result.get('matches', [])
    logger.info(f"当前可用匹配模式数: {len(matches)}")
    for m in matches:
        mode_id = m.get('mode_id', 0)
        info = MATCH_MODE_TABLE.get(mode_id)
        if info:
            desc = f"{info[1]}{info[2]}"
        else:
            desc = "unknown"
        playing = m.get('playing_count', 0)
        logger.info(f"  mode_id={mode_id} ({desc}) playing={playing}")


async def main():
    lobby, channel, version_clean = await connect_and_login()
    if not lobby:
        return
    
    try:
        # 1. 先看看有哪些可用模式
        await test_fetch_current_match(lobby)
        
        # 2. 尝试 start_unified_match
        result = await test_unified_match(lobby, version_clean)
        if result:
            logger.info(f"🎉 start_unified_match 成功! match_sid={result}")
            return
        
        # 3. 尝试 match_game
        result = await test_match_game(lobby, version_clean)
        if result:
            logger.info(f"🎉 match_game 成功! mode_id={result}")
            return
        
        logger.error("所有匹配方式都失败了")
        
    finally:
        await channel.close()


if __name__ == "__main__":
    asyncio.run(main())
