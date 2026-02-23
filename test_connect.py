"""连接测试脚本 — 测试能否连通雀魂服务器并登录

用法: python test_connect.py -u 用户名 -p 密码
"""
import asyncio
import hashlib
import hmac
import logging
import random
import sys
import uuid

import aiohttp

sys.path.insert(0, ".")
from ms.base import MSRPCChannel
from ms.rpc import Lobby
import ms.protocol_pb2 as pb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test")

MS_HOST = "https://game.maj-soul.com"


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    args = parser.parse_args()

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Step 1: 版本
        logger.info("Step 1: 获取版本...")
        async with session.get(f"{MS_HOST}/1/version.json") as res:
            version_info = await res.json()
            version = version_info["version"]
            version_clean = version.replace(".w", "")
            logger.info(f"  版本: {version}")

        # Step 2: 配置
        logger.info("Step 2: 获取服务器配置...")
        async with session.get(f"{MS_HOST}/1/v{version}/config.json") as res:
            config = await res.json()
            gateways = config["ip"][0]["gateways"]
            logger.info(f"  路由网关数: {len(gateways)}")

        # Step 3: 查找可用的路由节点
        logger.info("Step 3: 查询路由节点...")
        gateway = random.choice(gateways)
        gateway_url = gateway["url"]
        async with session.get(
            f"{gateway_url}/api/clientgate/routes"
            f"?platform=Web&version={version_clean}"
        ) as res:
            route_data = await res.json()
            routes = route_data["data"]["routes"]
            idle_routes = [r for r in routes if r["state"] == "idle"]
            if not idle_routes:
                idle_routes = routes
            logger.info(f"  可用路由: {len(idle_routes)} 个")

        route = random.choice(idle_routes)
        domain = route["domain"]
        ssl = route.get("ssl", True)
        scheme = "wss" if ssl else "ws"
        endpoint = f"{scheme}://{domain}/"
        logger.info(f"Step 4: 连接 WebSocket: {endpoint}")

        channel = MSRPCChannel(endpoint)
        lobby = Lobby(channel)

        try:
            await channel.connect(MS_HOST)
            logger.info("  ✅ WebSocket 连接成功!")
        except Exception as e:
            logger.error(f"  ❌ WebSocket 连接失败: {e}")
            return

        # Step 5: 登录
        logger.info(f"Step 5: 登录: {args.username}")

        uuid_key = str(uuid.uuid1())
        req = pb.ReqLogin()
        req.account = args.username
        req.password = hmac.new(
            b"lailai", args.password.encode(), hashlib.sha256
        ).hexdigest()
        req.device.is_browser = True
        req.random_key = uuid_key
        req.gen_access_token = True
        req.client_version_string = f"web-{version_clean}"
        req.currency_platforms.append(2)

        try:
            res = await lobby.login(req)
        except Exception as e:
            logger.error(f"  ❌ 登录请求失败: {e}")
            await channel.close()
            return

        if not res.access_token:
            logger.error(f"  ❌ 登录失败!")
            logger.error(f"  error: {res.error if hasattr(res, 'error') else 'unknown'}")
            await channel.close()
            return

        account_id = res.account_id
        nickname = res.account.nickname if hasattr(res, "account") and res.account else "?"
        logger.info(f"  ✅ 登录成功!")
        logger.info(f"  账号ID: {account_id}")
        logger.info(f"  昵称: {nickname}")

        # Step 6: 获取账号信息
        logger.info("Step 6: 获取账号信息...")
        try:
            info_req = pb.ReqAccountInfo()
            info_req.account_id = account_id
            info_res = await lobby.fetch_account_info(info_req)
            if hasattr(info_res, "account") and info_res.account:
                acc = info_res.account
                logger.info(f"  昵称: {acc.nickname}")
                logger.info(f"  等级ID: {acc.level.id if hasattr(acc, 'level') else '?'}")
            else:
                logger.info("  (无详细信息)")
        except Exception as e:
            logger.warning(f"  获取信息失败: {e}")

        # 完成
        await channel.close()
        logger.info("")
        logger.info("=" * 50)
        logger.info("🎉 所有步骤测试通过！可以正常使用。")
        logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
