import asyncio
import logging
import websockets

from ms.protocol_pb2 import Wrapper

logger = logging.getLogger("majsoul.ws")


class MSRPCChannel:

    def __init__(self, endpoint):
        self._endpoint = endpoint
        self._req_events = {}
        self._new_req_idx = 1
        self._res = {}
        self._hooks = {}

        self._ws = None
        self._msg_dispatcher = None
        self._disconnected = asyncio.Event()  # 断线信号
        self._on_disconnect_cb = None  # 断线回调

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        # websockets 16.0: 用 .state 代替废弃的 .open
        try:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except (ImportError, AttributeError):
            # fallback for older versions
            return getattr(self._ws, 'open', False)

    def on_disconnect(self, callback):
        """注册断线回调 (async callable)"""
        self._on_disconnect_cb = callback

    def add_hook(self, msg_type, hook):
        if msg_type not in self._hooks:
            self._hooks[msg_type] = []
        self._hooks[msg_type].append(hook)

    def unwrap(self, wrapped):
        wrapper = Wrapper()
        wrapper.ParseFromString(wrapped)
        return wrapper

    def wrap(self, name, data):
        wrapper = Wrapper()
        wrapper.name = name
        wrapper.data = data
        return wrapper.SerializeToString()

    async def connect(self, ms_host):
        self._ws = await websockets.connect(
            self._endpoint,
            origin=ms_host,
            ping_interval=None,  # 禁用内建 ping — 雀魂服务器不一定回 pong
            ping_timeout=None,   # 用应用层心跳 (client.heartbeat_loop) 代替
        )
        self._msg_dispatcher = asyncio.create_task(self.dispatch_msg())

    async def close(self):
        self._msg_dispatcher.cancel()
        try:
            await self._msg_dispatcher
        except asyncio.CancelledError:
            pass
        finally:
            await self._ws.close()

    async def dispatch_msg(self):
        try:
            while True:
                msg = await self._ws.recv()
                type_byte = msg[0]
                if type_byte == 1:  # NOTIFY
                    wrapper = self.unwrap(msg[1:])
                    logger.debug(
                        f"NOTIFY: {wrapper.name} ({len(wrapper.data)} bytes, "
                        f"hooks={list(self._hooks.keys())})"
                    )
                    for hook in self._hooks.get(wrapper.name, []):
                        asyncio.create_task(hook(wrapper.data))
                elif type_byte == 2:  # REQUEST
                    wrapper = self.unwrap(msg[3:])
                    for hook in self._hooks.get(wrapper.name, []):
                        asyncio.create_task(hook(wrapper.data))
                elif type_byte == 3:  # RESPONSE
                    idx = int.from_bytes(msg[1:3], 'little')
                    if not idx in self._req_events:
                        continue
                    self._res[idx] = msg
                    self._req_events[idx].set()
        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK) as e:
            logger.warning(f"WebSocket 连接断开: {e}")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"dispatch_msg 异常: {e}")
        finally:
            # 通知断线
            self._disconnected.set()
            # 唤醒所有等待中的请求
            for evt in self._req_events.values():
                evt.set()
            if self._on_disconnect_cb:
                try:
                    asyncio.create_task(self._on_disconnect_cb())
                except Exception:
                    pass

    async def send_request(self, name, msg):
        idx = self._new_req_idx
        self._new_req_idx = (self._new_req_idx + 1) % 60007

        wrapped = self.wrap(name, msg)
        pkt = b'\x02' + idx.to_bytes(2, 'little') + wrapped

        evt = asyncio.Event()
        self._req_events[idx] = evt

        await self._ws.send(pkt)
        await evt.wait()

        if not idx in self._res:
            return None
        res = self._res[idx]
        del self._res[idx]

        if idx in self._req_events:
            del self._req_events[idx]

        body = self.unwrap(res[3:])

        return body.data


class MSRPCService:

    def __init__(self, channel):
        self._channel = channel

    def get_package_name(self):
        raise NotImplementedError

    def get_service_name(self):
        raise NotImplementedError

    def get_req_class(self, method):
        raise NotImplementedError

    def get_res_class(self, method):
        raise NotImplementedError

    async def call_method(self, method, req):
        msg = req.SerializeToString()
        name = '.{}.{}.{}'.format(self.get_package_name(), self.get_service_name(), method)
        res_msg = await self._channel.send_request(name, msg)
        if res_msg is None:
            raise ConnectionError(f"RPC call {name} failed: connection lost")
        res_class = self.get_res_class(method)
        res = res_class()
        res.ParseFromString(res_msg)
        return res
