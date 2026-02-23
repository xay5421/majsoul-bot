"""mjai 协议适配器

将雀魂的游戏状态转换为 mjai 格式，与外部 AI 引擎通信。

mjai 协议参考:
- https://mjai.app/docs/mjai-protocol
- https://github.com/Equim-chan/Mortal/blob/main/libriichi/src/mjai/event.rs

牌编码: 与雀魂一致 (1m, 2p, 0s=赤五索, 1z=东, ..., 7z=中)
mjai 特殊写法: 赤宝牌 = 5mr, 5pr, 5sr; 字牌 E/S/W/N/P/F/C (东南西北白发中)
"""
import json
import logging
from typing import Any

logger = logging.getLogger("majsoul.mjai")

# ─── 牌编码转换 ─────────────────────────────────────

# 雀魂 → mjai
_MAJSOUL_TO_MJAI_HONORS = {
    "1z": "E", "2z": "S", "3z": "W", "4z": "N",
    "5z": "P", "6z": "F", "7z": "C",
}
# mjai → 雀魂
_MJAI_TO_MAJSOUL_HONORS = {v: k for k, v in _MAJSOUL_TO_MJAI_HONORS.items()}


def majsoul_to_mjai(tile: str) -> str:
    """雀魂牌编码 → mjai 牌编码

    '1m' → '1m', '0m' → '5mr', '1z' → 'E'
    """
    if not tile:
        return tile
    # 字牌
    if tile in _MAJSOUL_TO_MJAI_HONORS:
        return _MAJSOUL_TO_MJAI_HONORS[tile]
    # 赤宝牌
    if tile[0] == "0":
        return f"5{tile[1]}r"
    return tile


def mjai_to_majsoul(tile: str) -> str:
    """mjai 牌编码 → 雀魂牌编码

    '1m' → '1m', '5mr' → '0m', 'E' → '1z'
    """
    if not tile:
        return tile
    # 字牌 (单字母)
    if tile in _MJAI_TO_MAJSOUL_HONORS:
        return _MJAI_TO_MAJSOUL_HONORS[tile]
    # 赤宝牌
    if len(tile) == 3 and tile.endswith("r"):
        return f"0{tile[1]}"
    return tile


def majsoul_tiles_to_mjai(tiles: list[str]) -> list[str]:
    """批量转换"""
    return [majsoul_to_mjai(t) for t in tiles]


def mjai_tiles_to_majsoul(tiles: list[str]) -> list[str]:
    """批量转换"""
    return [mjai_to_majsoul(t) for t in tiles]


# ─── 场风编码 ─────────────────────────────────────

_WIND_TO_MJAI = {0: "E", 1: "S", 2: "W", 3: "N"}


# ─── mjai 事件构造 ──────────────────────────────────

def make_start_game(player_id: int, names: list[str] = None) -> dict:
    """构造 start_game 事件"""
    if names is None:
        names = ["player0", "player1", "player2", "player3"]
    return {
        "type": "start_game",
        "id": player_id,
        "names": names,
    }


def make_start_kyoku(round_wind: int, round_num: int, honba: int,
                     riichi_sticks: int, dealer: int, scores: list[int],
                     dora_indicator: str, tehais: list[list[str]],
                     player_id: int) -> dict:
    """构造 start_kyoku 事件

    tehais: 四家手牌，非本家用 ["?", "?", ...] 填充
    """
    # mjai 的 kyoku 从 1 开始
    kyoku = round_num + 1

    # 构造四家手牌（只有自己的是明的）
    mjai_tehais = []
    for i in range(4):
        if i < len(tehais) and tehais[i]:
            mjai_tehais.append(majsoul_tiles_to_mjai(tehais[i]))
        else:
            mjai_tehais.append(["?"] * 13)

    return {
        "type": "start_kyoku",
        "bakaze": _WIND_TO_MJAI[round_wind],
        "kyoku": kyoku,
        "honba": honba,
        "kyotaku": riichi_sticks,
        "oya": dealer,
        "scores": scores,
        "dora_marker": majsoul_to_mjai(dora_indicator),
        "tehais": mjai_tehais,
    }


def make_tsumo(actor: int, tile: str, is_self: bool = True) -> dict:
    """构造 tsumo 事件"""
    return {
        "type": "tsumo",
        "actor": actor,
        "pai": majsoul_to_mjai(tile) if is_self else "?",
    }


def make_dahai(actor: int, tile: str, tsumogiri: bool = False) -> dict:
    """构造 dahai 事件"""
    return {
        "type": "dahai",
        "actor": actor,
        "pai": majsoul_to_mjai(tile),
        "tsumogiri": tsumogiri,
    }


def make_chi(actor: int, target: int, tile: str,
             consumed: list[str]) -> dict:
    """构造 chi 事件"""
    return {
        "type": "chi",
        "actor": actor,
        "target": target,
        "pai": majsoul_to_mjai(tile),
        "consumed": majsoul_tiles_to_mjai(consumed),
    }


def make_pon(actor: int, target: int, tile: str,
             consumed: list[str]) -> dict:
    """构造 pon 事件"""
    return {
        "type": "pon",
        "actor": actor,
        "target": target,
        "pai": majsoul_to_mjai(tile),
        "consumed": majsoul_tiles_to_mjai(consumed),
    }


def make_daiminkan(actor: int, target: int, tile: str,
                   consumed: list[str]) -> dict:
    """构造 daiminkan (大明杠) 事件"""
    return {
        "type": "daiminkan",
        "actor": actor,
        "target": target,
        "pai": majsoul_to_mjai(tile),
        "consumed": majsoul_tiles_to_mjai(consumed),
    }


def make_ankan(actor: int, consumed: list[str]) -> dict:
    """构造 ankan (暗杠) 事件"""
    return {
        "type": "ankan",
        "actor": actor,
        "consumed": majsoul_tiles_to_mjai(consumed),
    }


def make_kakan(actor: int, tile: str, consumed: list[str]) -> dict:
    """构造 kakan (加杠) 事件"""
    return {
        "type": "kakan",
        "actor": actor,
        "pai": majsoul_to_mjai(tile),
        "consumed": majsoul_tiles_to_mjai(consumed),
    }


def make_dora(dora_marker: str) -> dict:
    """构造 dora 事件"""
    return {
        "type": "dora",
        "dora_marker": majsoul_to_mjai(dora_marker),
    }


def make_reach(actor: int) -> dict:
    """构造 reach (立直宣言) 事件"""
    return {
        "type": "reach",
        "actor": actor,
    }


def make_reach_accepted(actor: int) -> dict:
    """构造 reach_accepted (立直成立) 事件"""
    return {
        "type": "reach_accepted",
        "actor": actor,
    }


def make_hora(actor: int, target: int, deltas: list[int] = None,
              ura_markers: list[str] = None) -> dict:
    """构造 hora (和了) 事件"""
    event = {
        "type": "hora",
        "actor": actor,
        "target": target,
    }
    if deltas is not None:
        event["deltas"] = deltas
    if ura_markers is not None:
        event["ura_markers"] = majsoul_tiles_to_mjai(ura_markers)
    return event


def make_ryukyoku(deltas: list[int] = None) -> dict:
    """构造 ryukyoku (流局) 事件"""
    event = {"type": "ryukyoku"}
    if deltas is not None:
        event["deltas"] = deltas
    return event


def make_end_kyoku() -> dict:
    return {"type": "end_kyoku"}


def make_end_game() -> dict:
    return {"type": "end_game"}


# ─── 解析 AI 返回的 mjai 动作 ────────────────────────

def parse_mjai_action(action: dict) -> dict:
    """解析 mjai AI 返回的动作，转换为内部格式

    Returns:
        {
            "type": "dahai" | "reach" | "chi" | "pon" | "daiminkan" |
                    "ankan" | "kakan" | "hora" | "ryukyoku" | "none",
            "tile": str (majsoul format),
            "consumed": list[str],
            ...
        }
    """
    action_type = action.get("type", "none")

    if action_type == "dahai":
        return {
            "type": "dahai",
            "tile": mjai_to_majsoul(action["pai"]),
            "tsumogiri": action.get("tsumogiri", False),
        }
    elif action_type == "reach":
        return {"type": "reach"}
    elif action_type == "chi":
        return {
            "type": "chi",
            "tile": mjai_to_majsoul(action["pai"]),
            "consumed": mjai_tiles_to_majsoul(action["consumed"]),
        }
    elif action_type == "pon":
        return {
            "type": "pon",
            "tile": mjai_to_majsoul(action["pai"]),
            "consumed": mjai_tiles_to_majsoul(action["consumed"]),
        }
    elif action_type in ("daiminkan", "kakan", "ankan"):
        result = {"type": action_type}
        if "consumed" in action:
            result["consumed"] = mjai_tiles_to_majsoul(action["consumed"])
        if "pai" in action:
            result["tile"] = mjai_to_majsoul(action["pai"])
        return result
    elif action_type == "hora":
        return {"type": "hora"}
    elif action_type == "ryukyoku":
        return {"type": "ryukyoku"}
    elif action_type == "none":
        return {"type": "none"}
    else:
        logger.warning(f"未知的 mjai 动作类型: {action_type}")
        return {"type": "none"}
