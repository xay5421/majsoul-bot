"""人类行为模拟 — 操作延迟 + 节奏控制 + 行为变异"""
import random
import logging

logger = logging.getLogger("majsoul.human")


class HumanBehavior:
    """模拟人类打牌行为

    设计原则：
    1. 延迟范围贴近真人（真人快的 1-2s，慢的 5-10s，偶尔 AFK）
    2. 同一局内延迟有 correlation（手感好→快，思考多→慢）
    3. 局间有疲劳曲线（越打越慢/越快都有可能）
    4. 偶尔有极端值（走神 3-5s，或手速特快 0.5s）
    """

    def __init__(self):
        self._tempo: float = 1.0          # 当前节奏因子 (0.7~1.5)
        self._round_count: int = 0        # 当前对局内的局数
        self._fatigue: float = 0.0        # 疲劳度 (0~1)
        self._turn_count: int = 0         # 当前局内巡目
        self._session_games: int = 0      # 当前 session 打了几局

    def on_game_start(self) -> None:
        """对局开始：重置节奏"""
        self._round_count = 0
        # 每局随机一个基础节奏 (有人打快，有人打慢)
        self._tempo = random.uniform(0.8, 1.3)
        self._session_games += 1
        logger.debug(f"tempo={self._tempo:.2f}, session_games={self._session_games}")

    def on_round_start(self) -> None:
        """新一局：微调节奏"""
        self._round_count += 1
        self._turn_count = 0
        # 局数越多，节奏可能变化（有人越打越快，有人越打越慢）
        drift = random.gauss(0, 0.05)
        self._tempo = max(0.6, min(1.6, self._tempo + drift))
        # 疲劳度随局数增加
        self._fatigue = min(1.0, self._round_count * 0.05 + random.uniform(0, 0.1))

    def on_action(self) -> None:
        """每次操作后"""
        self._turn_count += 1

    def _jitter(self, base_min: float, base_max: float,
                tempo_factor: bool = True) -> float:
        """生成带抖动的延迟

        偶尔产生极端值模拟走神/手速快
        """
        base = random.uniform(base_min, base_max)

        if tempo_factor:
            base *= self._tempo

        # 5% 概率走神 (额外 2-6s)
        if random.random() < 0.05:
            base += random.uniform(2.0, 6.0)
            logger.debug(f"走神延迟: +{base - base_min:.1f}s")

        # 10% 概率特别快 (减半)
        if random.random() < 0.10:
            base *= 0.5

        # 疲劳影响 (越疲劳越慢)
        base *= (1.0 + self._fatigue * 0.3)

        return max(0.3, base)  # 最低 0.3s，太快不像人

    def get_discard_delay(self, is_tsumogiri: bool = False,
                          hand_size: int = 13,
                          is_riichi: bool = False) -> float:
        """出牌延迟

        真人参考：
        - 摸切/立直后: 0.5-1.5s (不用想)
        - 普通出牌: 1-4s
        - 复杂牌局: 2-6s
        """
        if is_riichi:
            # 立直后自动摸切，只需确认
            return self._jitter(0.3, 0.8)
        if is_tsumogiri:
            # 摸切：看一眼就打，但也不能太快
            return self._jitter(0.3, 1.2)

        # 普通出牌：根据巡目动态调整
        if self._turn_count < 3:
            # 开局几巡，配牌阶段思考多
            return self._jitter(1.0, 2.0)
        elif self._turn_count > 12:
            # 终盘，紧张/着急
            return self._jitter(0.5, 2.0)
        else:
            # 中盘，正常思考
            return self._jitter(0.7, 2.0)

    def get_call_delay(self, call_type: str = "pon") -> float:
        """吃碰杠荣和延迟

        真人参考：
        - 荣和/自摸: 0.5-2s (激动!)
        - 碰/杠: 1-3s (要想一下)
        - 吃: 1-4s (更要想)
        """
        if call_type in ("ron", "tsumo"):
            return self._jitter(0.3, 1.3)
        elif call_type == "pon":
            return self._jitter(0.7, 2.1)
        elif call_type == "kan":
            return self._jitter(0.5, 1.7)
        elif call_type == "chi":
            return self._jitter(0.7, 2.1)
        return self._jitter(0.7, 2.0)

    def get_skip_delay(self) -> float:
        """跳过操作延迟

        真人看到别人出牌后，即使不吃碰也要扫一眼
        """
        return self._jitter(0.2, 1.0)

    def get_riichi_delay(self) -> float:
        """立直决策延迟

        立直是大决策，真人通常想 2-5 秒
        """
        return self._jitter(1.0, 2.3)

    def get_new_round_delay(self) -> float:
        """新一局确认延迟

        看结算画面 + 点确认
        """
        return self._jitter(1.3, 2.7)

    def get_game_interval(self, config) -> float:
        """局间间隔（两局之间的等待时间）

        模拟真人在大厅逗留：看战绩、看好友、发呆...
        偶尔上厕所/喝水 → 更长等待
        """
        base_min = getattr(config, 'game_interval_min', 45)
        base_max = getattr(config, 'game_interval_max', 180)
        interval = random.uniform(base_min, base_max)

        # 15% 概率"上厕所" → 额外 60-180s
        if random.random() < 0.15:
            extra = random.uniform(60, 180)
            interval += extra
            logger.info(f"模拟 AFK: 额外等待 {extra:.0f}s")

        # 越打越疲劳 → 间隔变长
        interval *= (1.0 + self._fatigue * 0.5)

        return interval

    def should_take_session_break(self, config) -> bool:
        """判断是否该进入 session 休息"""
        games_min = getattr(config, 'session_games_min', 6)
        games_max = getattr(config, 'session_games_max', 15)
        # 在 min-max 范围内随机决定
        if self._session_games < games_min:
            return False
        if self._session_games >= games_max:
            return True
        # 打得越多越可能休息
        prob = (self._session_games - games_min) / max(1, games_max - games_min)
        return random.random() < prob

    def get_session_break(self, config) -> float:
        """session 休息时长"""
        break_min = getattr(config, 'session_break_min', 300)
        break_max = getattr(config, 'session_break_max', 900)
        self._session_games = 0  # 重置 session 计数
        self._fatigue = 0        # 休息后疲劳清零
        return random.uniform(break_min, break_max)

    def is_active_hours(self, config) -> bool:
        """检查当前是否在活跃时段内"""
        import datetime
        now = datetime.datetime.now()
        hour = now.hour
        start = getattr(config, 'active_hour_start', 9)
        end = getattr(config, 'active_hour_end', 25)

        if end <= 24:
            # 正常时段 (如 9-24)
            return start <= hour < end
        else:
            # 跨日 (如 9-25 表示 9:00~次日1:00)
            return hour >= start or hour < (end - 24)

    def get_lobby_stay_time(self) -> float:
        """模拟在大厅停留的时间（用于 lobbyCostTime 上报）

        真人进大厅后会：看公告、看好友、翻商店...
        通常 5-30 秒才开始匹配
        """
        return random.uniform(3.0, 20.0)

    def get_match_ui_browse_time(self) -> float:
        """打开匹配界面到点击匹配的时间

        真人打开匹配 UI 后会看一眼段位、选模式、再点匹配
        """
        return random.uniform(1.5, 5.0)
