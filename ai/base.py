"""AI 基类"""
from abc import ABC, abstractmethod
from game_state import GameState


class BaseAI(ABC):
    """麻将 AI 基类"""

    @abstractmethod
    def decide_discard(self, state: GameState) -> str:
        """
        决定打哪张牌。

        Args:
            state: 当前游戏状态

        Returns:
            要打出的牌的编码，如 "1m", "5p"
        """
        ...

    @abstractmethod
    def decide_action(self, state: GameState, actions: dict) -> dict | None:
        """
        决定是否执行操作（吃/碰/杠/和/立直/九种九牌等）。

        Args:
            state: 当前游戏状态
            actions: 可选操作列表

        Returns:
            选择的操作，None 表示跳过
        """
        ...

    def on_game_start(self, state: GameState) -> None:
        """对局开始回调"""
        pass

    def on_round_start(self, state: GameState) -> None:
        """新一局开始回调"""
        pass

    def on_game_end(self, result: dict) -> None:
        """对局结束回调"""
        pass
