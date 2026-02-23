"""雀魂 ActionPrototype.data XOR 解密

来源: Akagi v2 mitm/bridge/majsoul/liqi.py
雀魂对实时对局事件的 data 字段做了 XOR 混淆。
"""

_KEYS = [0x84, 0x5e, 0x4e, 0x42, 0x39, 0xa2, 0x1f, 0x60, 0x1c]


def decode(data: bytes) -> bytes:
    """解密 ActionPrototype.data"""
    data = bytearray(data)
    for i in range(len(data)):
        u = (23 ^ len(data)) + 5 * i + _KEYS[i % len(_KEYS)] & 255
        data[i] ^= u
    return bytes(data)


def encode(data: bytes) -> bytes:
    """加密 (XOR 是对称的，encode == decode)"""
    return decode(data)
