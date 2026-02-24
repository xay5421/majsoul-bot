"""测试段位场匹配 v2 - 尝试调用完整的初始化流程后再匹配"""
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


async def main():
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

    # ─── Step 1: Login ───
    req = pb.ReqLogin()
    req.account = cfg.account.username
    req.password = hmac.new(b"lailai", cfg.account.password.encode(), hashlib.sha256).hexdigest()
    req.device.is_browser = True
    req.random_key = str(uuid.uuid1())
    req.gen_access_token = True
    req.client_version_string = f"web-{version_clean}"
    req.currency_platforms.append(2)
    req.reconnect = True

    res = await lobby.login(req)
    if not res.access_token:
        logger.error(f"Login failed: {MessageToDict(res)}")
        await channel.close()
        return

    logger.info(f"✅ Logged in: {res.account.nickname} (id={res.account_id})")
    logger.info(f"   Level (四麻): {res.account.level.id} score={res.account.level.score}")
    access_token = res.access_token

    # ─── Step 2: loginSuccess ───
    logger.info("=" * 60)
    logger.info("调用 loginSuccess...")
    try:
        ls_res = await lobby.login_success(pb.ReqCommon())
        ls_dict = MessageToDict(ls_res, preserving_proto_field_name=True)
        logger.info(f"loginSuccess 响应: {ls_dict}")
    except Exception as e:
        logger.error(f"loginSuccess 异常: {e}")

    # ─── Step 3: loginBeat ───
    logger.info("=" * 60)
    logger.info("调用 loginBeat...")
    try:
        lb_req = pb.ReqLoginBeat()
        lb_req.contract = ""
        lb_res = await lobby.login_beat(lb_req)
        lb_dict = MessageToDict(lb_res, preserving_proto_field_name=True)
        logger.info(f"loginBeat 响应: {lb_dict}")
    except Exception as e:
        logger.error(f"loginBeat 异常: {e}")

    # ─── Step 4: fetchInfo (the big init call) ───
    logger.info("=" * 60)
    logger.info("调用 fetchInfo...")
    try:
        fi_res = await lobby.fetch_info(pb.ReqCommon())
        fi_dict = MessageToDict(fi_res, preserving_proto_field_name=True)
        # Just show top-level keys, the response is huge
        logger.info(f"fetchInfo 顶层字段: {list(fi_dict.keys())}")
        if 'error' in fi_dict and fi_dict['error'].get('code'):
            logger.error(f"fetchInfo 错误: {fi_dict['error']}")
    except Exception as e:
        logger.error(f"fetchInfo 异常: {e}")

    # ─── Step 5: fetchServerSettings ───
    logger.info("=" * 60)
    logger.info("调用 fetchServerSettings...")
    try:
        ss_res = await lobby.fetch_server_settings(pb.ReqCommon())
        ss_dict = MessageToDict(ss_res, preserving_proto_field_name=True)
        logger.info(f"fetchServerSettings 顶层字段: {list(ss_dict.keys())}")
    except Exception as e:
        logger.error(f"fetchServerSettings 异常: {e}")

    # ─── Step 6: heatbeat ───
    logger.info("=" * 60)
    logger.info("发送心跳...")
    try:
        hb_req = pb.ReqHeatBeat()
        hb_req.no_operation_counter = 0
        hb_res = await lobby.heatbeat(hb_req)
        logger.info(f"心跳响应: {MessageToDict(hb_res, preserving_proto_field_name=True)}")
    except Exception as e:
        logger.error(f"心跳异常: {e}")

    await asyncio.sleep(1)

    # ─── Step 7: Now try matching ───
    logger.info("=" * 60)
    logger.info("🎯 尝试段位匹配 (startUnifiedMatch)...")
    logger.info("=" * 60)

    # 铜之间四人东: type=1, id=2
    test_cases = [
        ("1:2", "铜之间四人东"),
        ("1:1", "铜之间四人东免费"),
        ("1:3", "铜之间四人南"),
    ]

    for sid, desc in test_cases:
        req = pb.ReqStartUnifiedMatch()
        req.match_sid = sid
        req.client_version_string = f"web-{version_clean}"

        try:
            res = await lobby.start_unified_match(req)
            result = MessageToDict(res, preserving_proto_field_name=True)
            err_code = result.get('error', {}).get('code', 0)

            if err_code == 0:
                logger.info(f"✅ '{sid}' ({desc}): SUCCESS!")
                # Cancel immediately
                cancel = pb.ReqCancelUnifiedMatch()
                cancel.match_sid = sid
                await lobby.cancel_unified_match(cancel)
                logger.info(f"   已取消匹配")
                break
            elif err_code == 1302:
                logger.info(f"⚡ '{sid}' ({desc}): 已在队列中 (1302)")
                cancel = pb.ReqCancelUnifiedMatch()
                cancel.match_sid = sid
                await lobby.cancel_unified_match(cancel)
                break
            else:
                logger.warning(f"❌ '{sid}' ({desc}): error={err_code} {result}")
        except Exception as e:
            logger.error(f"💥 '{sid}' ({desc}): exception={e}")

        await asyncio.sleep(0.5)

    # ─── Step 8: Also try matchGame (old API) ───
    logger.info("=" * 60)
    logger.info("🎯 尝试段位匹配 (matchGame / ReqJoinMatchQueue)...")
    logger.info("=" * 60)

    for mode_id, desc in [(2, "铜之间四人东"), (1, "铜之间四人东免费"), (3, "铜之间四人南")]:
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
                break
            elif err_code == 1302:
                logger.info(f"⚡ mode={mode_id} ({desc}): 已在队列中 (1302)")
                cancel = pb.ReqCancelMatchQueue()
                await lobby.cancel_match(cancel)
                break
            else:
                logger.warning(f"❌ mode={mode_id} ({desc}): error={err_code}")
        except Exception as e:
            logger.error(f"💥 mode={mode_id} ({desc}): exception={e}")

        await asyncio.sleep(0.5)

    await channel.close()
    logger.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
