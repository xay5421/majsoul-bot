"""测试 game server 连接: 尝试所有 route + /gateway 路径 + session 参数"""
import asyncio, logging
import ms.protocol_pb2 as pb
from google.protobuf.json_format import MessageToDict
from ms.base import MSRPCChannel
from ms.rpc import Lobby, FastTest
from client import MajsoulClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger("test")

async def main():
    c = MajsoulClient()
    await c.connect()
    await c.login('2449670833@qq.com', 'wxwakioi')
    log.info(f"access_token: {c.access_token[:20]}...")

    # 清理残留
    ft = FastTest(c.channel)
    for _ in range(2):
        try: await asyncio.wait_for(ft.terminate_game(pb.ReqCommon()), 3)
        except: pass
        try: await c.lobby.leave_room(pb.ReqCommon())
        except: pass
        await asyncio.sleep(0.5)

    gi = await c.lobby.fetch_gaming_info(pb.ReqCommon())
    if MessageToDict(gi, preserving_proto_field_name=True).get('game_info',{}).get('connect_token'):
        log.error("残留对局未清理!"); await c.close(); return

    # === 准备 ===
    done = asyncio.Event()
    success_ch = None

    async def on_game_start(data):
        nonlocal success_ch
        msg = pb.NotifyRoomGameStart()
        msg.ParseFromString(data)
        log.info(f"🎮 game_url={msg.game_url} token={msg.connect_token[:16]}...")

        # routes 列表
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(f'https://game.maj-soul.com/1/version.json') as r:
                ver = (await r.json())['version']
            async with s.get(f'https://route-2.maj-soul.com/api/clientgate/routes?platform=Web&version={ver}') as r:
                routes = (await r.json())['data']['routes']

        # 每个 route 的 /gateway 和 / 都试
        for route in routes:
            domain = route['domain']
            for path in ['/gateway', '/']:
                url = f'wss://{domain}{path}'
                try:
                    log.info(f"  {url} ...")
                    game_ch = MSRPCChannel(url)
                    await asyncio.wait_for(game_ch.connect('https://game.maj-soul.com'), timeout=5)

                    gft = FastTest(game_ch)
                    auth_req = pb.ReqAuthGame()
                    auth_req.account_id = c.account_id
                    auth_req.token = msg.connect_token
                    auth_req.game_uuid = msg.game_uuid
                    auth_req.session = c.access_token

                    auth_res = await gft.auth_game(auth_req)
                    ad = MessageToDict(auth_res, preserving_proto_field_name=True)
                    err = ad.get('error',{}).get('code',0)
                    log.info(f"    code={err}")

                    if err == 0:
                        seats = ad.get('seat_list',[])
                        log.info(f"    ✅ seats={seats}")
                        success_ch = game_ch
                        done.set()
                        return
                    await game_ch.close()
                except asyncio.TimeoutError:
                    log.info(f"    timeout")
                except Exception as e:
                    log.info(f"    err: {str(e)[:60]}")

        log.error("全部失败")
        done.set()

    c.channel.add_hook('.lq.NotifyRoomGameStart', on_game_start)

    # 创建房间
    req = pb.ReqCreateRoom()
    req.player_count = 4; req.mode.mode = 2; req.mode.ai = True
    req.client_version_string = c.version
    dr = req.mode.detail_rule
    dr.time_fixed = 5; dr.time_add = 20; dr.dora_count = 3; dr.shiduan = 1
    dr.init_point = 25000; dr.fandian = 30000; dr.can_jifei = True
    dr.have_liujumanguan = True; dr.have_biao_dora = True
    dr.have_gang_biao_dora = True; dr.have_li_dora = True
    dr.have_gang_li_dora = True

    res = await c.lobby.create_room(req)
    d = MessageToDict(res, preserving_proto_field_name=True)
    if d.get('error'): log.error(f"createRoom: {d['error']}"); await c.close(); return
    log.info(f"Room {d['room']['room_id']}")

    for i in range(3):
        await c.lobby.add_room_robot(pb.ReqAddRoomRobot(position=i+1))

    await c.lobby.start_room(pb.ReqRoomStart())
    log.info("Waiting for NotifyRoomGameStart...")

    try: await asyncio.wait_for(done.wait(), timeout=30)
    except asyncio.TimeoutError: log.error("TIMEOUT")

    if success_ch:
        log.info("🎉 Game server connected! Testing enterGame...")
        gft2 = FastTest(success_ch)
        enter = await gft2.enter_game(pb.ReqCommon())
        ed = MessageToDict(enter, preserving_proto_field_name=True)
        log.info(f"enterGame: is_end={ed.get('is_end')}, keys={list(ed.keys())}")
        await success_ch.close()

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
