"""麻将牌编码/解码工具

雀魂的牌编码格式: XY
- X = 数字 (0-9), 0 表示赤宝牌
- Y = 花色: m(万), p(饼), s(索), z(字)

字牌编号: 1z=东, 2z=南, 3z=西, 4z=北, 5z=白, 6z=发, 7z=中

示例:
- "1m" = 一万
- "5p" = 五饼
- "0s" = 赤五索
- "7z" = 中
"""

# 花色映射
SUIT_NAMES = {"m": "万", "p": "饼", "s": "索", "z": "字"}
WIND_NAMES = {1: "东", 2: "南", 3: "西", 4: "北"}
DRAGON_NAMES = {5: "白", 6: "发", 7: "中"}
HONOR_NAMES = {**WIND_NAMES, **DRAGON_NAMES}


def tile_to_str(tile: str) -> str:
    """将牌编码转换为中文显示 '1m' -> '一万'"""
    if not tile or len(tile) < 2:
        return tile
    num = tile[:-1]
    suit = tile[-1]
    if suit == "z":
        n = int(num)
        return HONOR_NAMES.get(n, tile)
    elif num == "0":
        return f"赤五{SUIT_NAMES.get(suit, suit)}"
    else:
        cn_nums = "〇一二三四五六七八九"
        n = int(num)
        return f"{cn_nums[n]}{SUIT_NAMES.get(suit, suit)}"


def tiles_to_str(tiles: list[str]) -> str:
    """将一组牌编码转换为中文显示"""
    return " ".join(tile_to_str(t) for t in tiles)


def sort_tiles(tiles: list[str]) -> list[str]:
    """对手牌排序: 万 < 饼 < 索 < 字, 同花色按数字排"""
    suit_order = {"m": 0, "p": 1, "s": 2, "z": 3}

    def sort_key(tile):
        suit = tile[-1]
        num = tile[:-1]
        # 赤宝牌排在普通 5 前面
        n = int(num) if num != "0" else 4.5
        return (suit_order.get(suit, 9), n)

    return sorted(tiles, key=sort_key)


def count_tiles(tiles: list[str]) -> dict[str, int]:
    """统计各牌出现次数"""
    counts: dict[str, int] = {}
    for t in tiles:
        # 将赤宝牌归一化到普通牌计数（但保留赤标记）
        key = t
        counts[key] = counts.get(key, 0) + 1
    return counts


def normalize_aka(tile: str) -> str:
    """将赤宝牌归一化为普通牌 '0m' -> '5m'"""
    if tile and tile[0] == "0":
        return "5" + tile[1:]
    return tile


def is_aka(tile: str) -> bool:
    """是否赤宝牌"""
    return tile and tile[0] == "0"


def tile_number(tile: str) -> int:
    """获取牌的数字，赤宝牌返回5"""
    if tile[0] == "0":
        return 5
    return int(tile[:-1])


def tile_suit(tile: str) -> str:
    """获取牌的花色"""
    return tile[-1]


def is_honor(tile: str) -> bool:
    """是否字牌"""
    return tile_suit(tile) == "z"


def is_terminal(tile: str) -> bool:
    """是否幺九牌（1或9的数牌，或字牌）"""
    if is_honor(tile):
        return True
    n = tile_number(tile)
    return n == 1 or n == 9


def is_simple(tile: str) -> bool:
    """是否中张牌（2-8的数牌）"""
    return not is_terminal(tile)


# 所有牌的列表（不含赤宝牌）
ALL_TILES = []
for suit in ["m", "p", "s"]:
    for num in range(1, 10):
        ALL_TILES.append(f"{num}{suit}")
for num in range(1, 8):
    ALL_TILES.append(f"{num}z")


def remaining_tiles(visible: list[str], tile: str) -> int:
    """计算某张牌还剩多少（总共4张减去可见的）"""
    norm = normalize_aka(tile)
    count = sum(1 for t in visible if normalize_aka(t) == norm)
    return 4 - count
