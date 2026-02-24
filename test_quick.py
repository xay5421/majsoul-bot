"""Quick test: login + init + match"""
import asyncio, hashlib, hmac, uuid, random, aiohttp, logging
from google.protobuf.json_format import MessageToDict
from ms.base import MSRPCChannel
from ms.rpc import Lobby
import ms.protocol_pb2 as pb
from config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

async def main():
    cfg = load_config()
    async with aiohttp.ClientSession() as session:
        async with session.get("https://game.maj-soul.com/1/version.json") as res:
            v = await res.json()
            version = v["version"]
            vc = version.replace(".w", "")
        async with session.get(f"https://game.maj-soul.com/1/v{version}/config.json") as res:
            config = await res.json()
            gws = config["ip"][0]["gateways"]
        gw = random.choice(gws)
        async with session.get(f"{gw['url']}/api/clientgate/routes?platform=Web&version={vc}") as res:
            rd = await res.json()
            routes = rd["data"]["routes"]
            idle = [r for r in routes if r["state"] == "idle"] or routes
            route = random.choice(idle)
            ep = f"{'wss' if route.get('ssl', True) else 'ws'}://{route['domain']}/"

    ch = MSRPCChannel(ep)
    lobby = Lobby(ch)
    await ch.connect("https://game.maj-soul.com")

    req = pb.ReqLogin()
    req.account = cfg.account.username
    req.password = hmac.new(b"lailai", cfg.account.password.encode(), hashlib.sha256).hexdigest()
    req.device.is_browser = True
    req.random_key = str(uuid.uuid1())
    req.gen_access_token = True
    req.client_version_string = f"web-{vc}"
    req.currency_platforms.append(2)
    req.reconnect = True
    res = await lobby.login(req)
    print(f"Login: {res.account.nickname} id={res.account_id}")
    
    # Check stale
    has_stale = bool(res.game_info and res.game_info.connect_token)
    print(f"Stale game: {has_stale}")
    if has_stale:
        print(f"  game_uuid: {res.game_info.game_uuid[:40]}")

    # Init
    await lobby.login_success(pb.ReqCommon())
    print("loginSuccess OK")
    fi = await lobby.fetch_info(pb.ReqCommon())
    fi_d = MessageToDict(fi, preserving_proto_field_name=True)
    print(f"fetchInfo OK, keys: {list(fi_d.keys())[:5]}...")

    # Match
    req2 = pb.ReqStartUnifiedMatch()
    req2.match_sid = "1:2"
    req2.client_version_string = f"web-{vc}"
    res2 = await lobby.start_unified_match(req2)
    r = MessageToDict(res2, preserving_proto_field_name=True)
    err = r.get("error", {}).get("code", 0)
    print(f"Match result: error={err}")

    if err == 0:
        await asyncio.sleep(0.5)
        c = pb.ReqCancelUnifiedMatch()
        c.match_sid = "1:2"
        await lobby.cancel_unified_match(c)
        print("Cancelled")

    await ch.close()
    print("Done")

asyncio.run(main())
