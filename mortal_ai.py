"""Mortal AI 引擎 — 直接加载 Mortal 模型进行推理

依赖:
- mortal/mortal.pth (模型权重)
- mortal/libriichi.so (Rust 编译的 Python 扩展)
- torch (PyTorch CPU 版)
- numpy

libriichi 提供:
- mjai.Bot: 状态管理 + obs 编码
- consts: obs_shape, ACTION_SPACE 等常量
"""
import json
import sys
import pathlib
import logging

logger = logging.getLogger("majsoul.mortal")

# libriichi 路径
MORTAL_DIR = pathlib.Path(__file__).parent / "mortal"


def check_mortal_available() -> tuple[bool, str]:
    """检查 Mortal 依赖是否可用"""
    # 检查模型文件
    pth_file = MORTAL_DIR / "mortal.pth"
    if not pth_file.exists():
        return False, f"模型文件不存在: {pth_file}"

    # 检查 libriichi
    so_file = MORTAL_DIR / "libriichi.so"
    if not so_file.exists():
        return False, f"libriichi.so 不存在: {so_file}"

    # 检查 torch
    try:
        import torch
    except ImportError:
        return False, "缺少 PyTorch: pip install torch --index-url https://download.pytorch.org/whl/cpu"

    # 检查 numpy
    try:
        import numpy
    except ImportError:
        return False, "缺少 numpy: pip install numpy"

    return True, "OK"


def load_mortal_bot(seat: int):
    """加载 Mortal AI Bot

    Args:
        seat: 玩家座位号 (0-3)

    Returns:
        MortalBot 实例
    """
    import torch
    import numpy  # noqa: F401

    # 添加 mortal 目录到 Python 路径以便导入 libriichi
    mortal_dir = str(MORTAL_DIR)
    if mortal_dir not in sys.path:
        sys.path.insert(0, mortal_dir)

    # 导入 libriichi
    try:
        import libriichi
        from libriichi.mjai import Bot as LibriichBot
        from libriichi.consts import obs_shape, ACTION_SPACE
    except ImportError as e:
        raise ImportError(
            f"无法导入 libriichi: {e}\n"
            f"请确保 libriichi.so 在 {MORTAL_DIR} 目录下"
        ) from e

    # 导入模型定义（从 mortal/model.py）
    from model import Brain, DQN, MortalEngine

    # 加载模型权重
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    pth_file = MORTAL_DIR / "mortal.pth"
    logger.info(f"加载 Mortal 模型: {pth_file} (device={device})")

    state = torch.load(pth_file, map_location=device, weights_only=False)
    config = state['config']
    version = config['control']['version']

    logger.info(f"模型版本: {version}, "
                f"conv_channels={config['resnet']['conv_channels']}, "
                f"num_blocks={config['resnet']['num_blocks']}")

    # 构建网络
    brain = Brain(
        version=version,
        conv_channels=config['resnet']['conv_channels'],
        num_blocks=config['resnet']['num_blocks'],
    ).eval()
    dqn = DQN(version=version).eval()

    brain.load_state_dict(state['mortal'])
    dqn.load_state_dict(state['current_dqn'])

    # 创建引擎
    engine = MortalEngine(
        brain, dqn,
        is_oracle=False,
        version=version,
        device=device,
        enable_amp=False,
        enable_quick_eval=False,
        enable_rule_based_agari_guard=True,
        name='mortal',
    )

    # 创建 Bot
    bot = LibriichBot(engine, seat)
    logger.info(f"Mortal AI 加载成功! 座位={seat}")
    return MortalBot(bot, seat)


class MortalBot:
    """Mortal AI 的封装，接收 mjai 事件列表，返回 mjai 动作"""

    def __init__(self, libriichi_bot, seat: int):
        self._bot = libriichi_bot
        self.seat = seat

    def react(self, events: list[dict]) -> dict | None:
        """处理 mjai 事件并返回动作

        Args:
            events: mjai 格式事件列表

        Returns:
            mjai 格式动作 dict，或 None
        """
        result = None
        for event in events:
            if event["type"] == "end_game":
                return None

            event_json = json.dumps(event, separators=(",", ":"))
            try:
                action_json = self._bot.react(event_json)
                if action_json:
                    action = json.loads(action_json)
                    if action.get("type") != "none":
                        result = action
            except Exception as e:
                logger.error(f"Mortal react 错误: {e}")
                continue

        return result
