"""实时状态显示 — 对局中输出可读的局面信息"""
import logging
from tiles import tile_to_str, tiles_to_str, sort_tiles, WIND_NAMES
from ai.shanten import calc_shanten

logger = logging.getLogger("majsoul.display")

# ANSI 颜色
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

WINDS = ["東", "南", "西", "北"]


def format_tile(tile: str) -> str:
    """格式化单张牌显示（带颜色）"""
    if not tile:
        return "?"
    suit = tile[-1]
    num = tile[:-1]
    name = tile_to_str(tile)

    if num == "0":
        return f"{RED}{name}{RESET}"  # 赤宝牌红色
    elif suit == "m":
        return f"{BLUE}{name}{RESET}"
    elif suit == "p":
        return f"{GREEN}{name}{RESET}"
    elif suit == "s":
        return f"{CYAN}{name}{RESET}"
    elif suit == "z":
        return f"{YELLOW}{name}{RESET}"
    return name


def format_tiles(tiles: list[str]) -> str:
    """格式化多张牌"""
    return " ".join(format_tile(t) for t in tiles)


def format_hand(hand: list[str], draw: str | None = None) -> str:
    """格式化手牌（摸牌用分隔符隔开）"""
    sorted_hand = sort_tiles(hand)
    parts = format_tiles(sorted_hand)
    if draw:
        parts += f" │ {format_tile(draw)}"
    return parts


def format_meld(meld) -> str:
    """格式化副露"""
    return f"[{meld.type}: {format_tiles(meld.tiles)}]"


def format_score(score: int) -> str:
    """格式化点数"""
    if score >= 30000:
        return f"{GREEN}{score}{RESET}"
    elif score <= 10000:
        return f"{RED}{score}{RESET}"
    return str(score)


def show_round_start(gs) -> None:
    """局开始时的信息"""
    wind = WINDS[gs.round_wind]
    num = gs.round_num + 1
    my_wind = WINDS[gs.my_wind]

    print()
    print(f"{'═' * 60}")
    print(f"  {BOLD}🀄 {wind}{num}局 {gs.honba}本场{RESET}"
          f"  供托: {gs.riichi_sticks}本")
    print(f"{'═' * 60}")

    # 各家点数
    for i, p in enumerate(gs.players):
        marker = " ← 自家" if i == gs.seat else ""
        seat_wind = WINDS[(i - gs.dealer) % gs.player_count]
        dealer = " (庄)" if i == gs.dealer else ""
        print(f"  {seat_wind}{dealer} 玩家{i}: {format_score(p.score)}{marker}")

    print(f"  自风: {BOLD}{my_wind}{RESET}")
    print(f"  宝牌指示: {format_tiles(gs.dora_indicators)}")
    print()
    print(f"  手牌: {format_hand(gs.hand, gs.draw)}  │ {_fmt_shanten(gs.hand)}")
    print(f"{'─' * 60}")


def _fmt_shanten(tiles: list[str]) -> str:
    """格式化向听数显示"""
    try:
        s = calc_shanten(tiles)
        if s == -1:
            return f"{GREEN}和了{RESET}"
        elif s == 0:
            return f"{GREEN}听牌{RESET}"
        elif s <= 2:
            return f"{YELLOW}{s}向听{RESET}"
        else:
            return f"{DIM}{s}向听{RESET}"
    except Exception:
        return ""


def show_draw(gs, tile: str) -> None:
    """摸牌"""
    shanten_part = _fmt_shanten(gs.hand)
    shanten_sep = f"  │ {shanten_part}" if shanten_part else ""
    print(f"  {DIM}[巡{gs.turn:2d}]{RESET} 摸 {format_tile(tile)}"
          f"  │ 手牌: {format_hand(gs.hand, tile)}"
          f"{shanten_sep}"
          f"  │ 剩{gs.tiles_left}枚")


def show_discard(gs, seat: int, tile: str, is_tsumogiri: bool = False,
                 is_riichi: bool = False, mortal_meta: dict | None = None) -> None:
    """出牌"""
    who = f"玩家{seat}" if seat != gs.seat else f"{BOLD}自家{RESET}"
    moqie = f" {DIM}(摸切){RESET}" if is_tsumogiri else ""
    riichi = f" {RED}[立直!]{RESET}" if is_riichi else ""

    line = f"  {DIM}[巡{gs.turn:2d}]{RESET} {who} 打 {format_tile(tile)}{moqie}{riichi}"

    if seat == gs.seat:
        shanten_part = _fmt_shanten(gs.hand)
        line += f"  │ 手牌: {format_hand(gs.hand)}"
        if shanten_part:
            line += f"  │ {shanten_part}"

    print(line)

    # 如果有 Mortal meta，显示 AI 分析信息
    if mortal_meta and seat == gs.seat:
        shanten = mortal_meta.get("shanten")
        furiten = mortal_meta.get("at_furiten", False)
        eval_ns = mortal_meta.get("eval_time_ns", 0)
        eval_ms = eval_ns / 1_000_000

        parts = []
        if shanten is not None:
            if shanten == 0:
                parts.append(f"{GREEN}听牌{RESET}")
            elif shanten == -1:
                parts.append(f"{GREEN}和了{RESET}")
            else:
                parts.append(f"向听={shanten}")
        if furiten:
            parts.append(f"{RED}振听{RESET}")
        if eval_ms > 0:
            parts.append(f"思考{eval_ms:.1f}ms")

        if parts:
            print(f"         {DIM}AI: {' | '.join(parts)}{RESET}")


def show_call(gs, seat: int, call_type: str, tiles: list[str]) -> None:
    """吃碰杠"""
    who = f"玩家{seat}" if seat != gs.seat else f"{BOLD}自家{RESET}"
    type_emoji = {"吃": "🟢", "碰": "🔵", "杠": "🟡", "暗杠": "🟠"}.get(call_type, "⚪")
    print(f"  {DIM}[巡{gs.turn:2d}]{RESET} {type_emoji} {who} {call_type} {format_tiles(tiles)}")

    if seat == gs.seat:
        print(f"         手牌: {format_hand(gs.hand)}")


def show_win(gs, seat: int, tile: str, is_tsumo: bool = False,
             scores: list[int] | None = None) -> None:
    """和牌"""
    who = f"玩家{seat}" if seat != gs.seat else f"{BOLD}自家{RESET}"
    win_type = "自摸" if is_tsumo else "荣和"

    print()
    print(f"  {'🎊' * 5}")
    print(f"  {BOLD}{who} {win_type}! 和牌: {format_tile(tile)}{RESET}")

    if scores:
        print(f"  点数变动:")
        for i, s in enumerate(scores):
            marker = " ←" if i == gs.seat else ""
            delta = s - gs.players[i].score
            delta_str = f"+{delta}" if delta >= 0 else str(delta)
            color = GREEN if delta > 0 else RED if delta < 0 else ""
            reset = RESET if color else ""
            print(f"    玩家{i}: {gs.players[i].score} → {color}{s} ({delta_str}){reset}{marker}")
    print(f"  {'🎊' * 5}")
    print()


def show_ryuukyoku(gs, reason: str = "流局") -> None:
    """流局"""
    print()
    print(f"  {'─' * 40}")
    print(f"  {BOLD}💨 {reason}{RESET}")
    print(f"  {'─' * 40}")
    print()


def show_game_end(gs, final_scores: list[int] | None = None) -> None:
    """对局结束"""
    print()
    print(f"{'═' * 60}")
    print(f"  {BOLD}🏁 对局结束{RESET}")
    print(f"{'═' * 60}")

    if final_scores:
        # 按点数排名
        ranked = sorted(enumerate(final_scores), key=lambda x: -x[1])
        for rank, (i, score) in enumerate(ranked):
            marker = " ← 自家" if i == gs.seat else ""
            medal = ["🥇", "🥈", "🥉", "💀"][rank] if rank < 4 else ""
            print(f"  {medal} 第{rank + 1}名 玩家{i}: {format_score(score)}{marker}")
    print(f"{'═' * 60}")
    print()


def show_action_decision(action_type: str, detail: str = "") -> None:
    """AI 决策"""
    emoji = {
        "discard": "🎯",
        "chi": "🟢",
        "pon": "🔵",
        "kan": "🟡",
        "riichi": "⚡",
        "tsumo": "🎊",
        "ron": "🎊",
        "skip": "⏭️",
    }.get(action_type, "🤔")
    print(f"         {emoji} 决策: {action_type} {detail}")


def show_waiting(message: str = "等待中...") -> None:
    """等待状态"""
    print(f"  {DIM}⏳ {message}{RESET}", end="\r")
