"""配置管理"""
import os
import yaml
from dataclasses import dataclass, field


@dataclass
class AccountConfig:
    username: str = ""
    password: str = ""


@dataclass
class MatchConfig:
    mode: str = "rank"       # rank, casual, room
    room_type: str = "4e"    # 4e, 4s, 3e, 3s
    level: str = "copper"    # copper, silver, gold, jade, throne


@dataclass
class AIConfig:
    type: str = "shanten"    # basic, shanten, mortal, mjai_subprocess, mjai_http
    command: str = ""         # mjai_subprocess: AI 程序命令
    url: str = ""             # mjai_http: AI 服务器地址
    mortal_dir: str = ""      # mortal: Mortal 目录路径
    nerf_turns: int = 0       # 装弱：第一名时前 N 步用 ShantenAI（0=关闭）


@dataclass
class RunConfig:
    max_games: int = 1
    game_interval: int = 5
    log_level: str = "INFO"


@dataclass
class EmojiConfig:
    enabled: bool = True
    on_win: bool = True           # 和牌时发表情
    win_emojis: list = field(default_factory=lambda: [2, 6, 7])
    on_riichi: bool = True        # 立直时发表情
    riichi_emojis: list = field(default_factory=lambda: [3, 8])


@dataclass
class Config:
    account: AccountConfig = field(default_factory=AccountConfig)
    match: MatchConfig = field(default_factory=MatchConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    run: RunConfig = field(default_factory=RunConfig)
    emoji: EmojiConfig = field(default_factory=EmojiConfig)


def load_config(path: str = "config.yaml") -> Config:
    """加载配置文件"""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"配置文件 {path} 不存在，请复制 config.example.yaml 并填写账号信息"
        )

    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    config = Config()
    if "account" in raw:
        config.account = AccountConfig(**raw["account"])
    if "match" in raw:
        config.match = MatchConfig(**raw["match"])
    if "ai" in raw:
        config.ai = AIConfig(**raw["ai"])
    if "run" in raw:
        config.run = RunConfig(**raw["run"])
    if "emoji" in raw:
        config.emoji = EmojiConfig(**raw["emoji"])

    return config
