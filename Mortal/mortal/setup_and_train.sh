#!/bin/bash
# ================================================================
# Mortal 训练环境一键配置 & 训练脚本
# 
# 在你的 Ubuntu + GPU 机器上运行:
#   cd majsoul-bot/Mortal/mortal
#   chmod +x setup_and_train.sh
#   ./setup_and_train.sh setup    # 装环境 + 编译 libriichi
#   ./setup_and_train.sh data     # 下载天凤牌谱 + 转换
#   ./setup_and_train.sh grp      # 训练 GRP 模型
#   ./setup_and_train.sh train    # 训练 Mortal 主模型
#   ./setup_and_train.sh all      # 以上全部
# ================================================================

set -e

# mortal/ 目录
MORTAL_DIR="$(cd "$(dirname "$0")" && pwd)"
# Mortal 根目录 (Cargo.toml 所在)
MORTAL_ROOT="$(dirname "$MORTAL_DIR")"
# majsoul-bot 根目录
PROJECT_DIR="$(dirname "$MORTAL_ROOT")"
VENV_DIR="$PROJECT_DIR/.venv"
DATA_DIR="$MORTAL_DIR/data"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ================================================================
# Step 1: 环境配置
# ================================================================
setup_env() {
    info "=== 环境配置 ==="
    
    if ! command -v python3 &>/dev/null; then
        error "需要 Python 3.10+，请先安装"
    fi
    info "Python: $(python3 --version)"
    
    # Rust
    if ! command -v cargo &>/dev/null; then
        info "安装 Rust..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
        source "$HOME/.cargo/env"
    fi
    info "Rust: $(rustc --version)"
    
    # venv (共用 bot 的 venv)
    if [ ! -d "$VENV_DIR" ]; then
        info "创建 venv..."
        python3 -m venv "$VENV_DIR"
    fi
    source "$VENV_DIR/bin/activate"
    
    # PyTorch CUDA 12.x
    info "安装 PyTorch..."
    pip install --upgrade pip
    pip install torch --index-url https://download.pytorch.org/whl/cu124
    
    # 其他依赖
    pip install tqdm toml tensorboard numpy
    
    # 验证 CUDA
    python3 -c "
import torch
print(f'PyTorch {torch.__version__}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
    print(f'CUDA: {torch.version.cuda}')
else:
    print('⚠ CUDA 不可用，训练会很慢')
"
    
    # 编译 libriichi
    info "编译 libriichi..."
    cd "$MORTAL_ROOT"
    cargo build -p libriichi --lib --release
    cp target/release/libriichi.so "$MORTAL_DIR/libriichi.so"
    
    # 验证
    cd "$MORTAL_DIR"
    python3 -c "from libriichi.consts import obs_shape; print(f'libriichi OK, obs_shape v4={obs_shape(4)}')"
    
    info "✅ 环境配置完成！"
}

# ================================================================
# Step 2: 数据集
# ================================================================
prepare_data() {
    source "$VENV_DIR/bin/activate"
    info "=== 准备数据集 ==="
    cd "$DATA_DIR"
    python3 prepare_dataset.py --all
    info "✅ 数据集准备完成！"
}

# ================================================================
# Step 3: 训练 GRP
# ================================================================
train_grp() {
    source "$VENV_DIR/bin/activate"
    cd "$MORTAL_DIR"
    
    if [ -f "grp.pth" ]; then
        info "grp.pth 已存在，跳过（删除可重训）"
        return
    fi
    
    info "=== 训练 GRP 模型 ==="
    MORTAL_CFG=config_train.toml python3 train_grp.py
    info "✅ GRP 训练完成！"
}

# ================================================================
# Step 4: 训练 Mortal
# ================================================================
train_mortal() {
    source "$VENV_DIR/bin/activate"
    cd "$MORTAL_DIR"
    
    [ ! -f "grp.pth" ] && error "需要先训练 GRP: $0 grp"
    
    info "=== 训练 Mortal ==="
    info "架构: conv_channels=128, num_blocks=20 (~25M params)"
    info "TensorBoard: tensorboard --logdir runs/"
    
    MORTAL_CFG=config_train.toml python3 train.py
    
    info "✅ 训练完成！"
    info "模型: $MORTAL_DIR/train_state.pth"
    info "最佳: $MORTAL_DIR/best_state.pth"
}

# ================================================================
case "${1:-help}" in
    setup)  setup_env ;;
    data)   prepare_data ;;
    grp)    train_grp ;;
    train)  train_mortal ;;
    all)    setup_env; prepare_data; train_grp; train_mortal ;;
    *)
        echo "用法: $0 {setup|data|grp|train|all}"
        echo ""
        echo "  setup   装环境 + 编译 libriichi"
        echo "  data    下载天凤牌谱 + 转换 mjai"
        echo "  grp     训练 GRP 辅助模型"
        echo "  train   训练 Mortal 主模型"
        echo "  all     全部按顺序执行"
        echo ""
        echo "训练完后导出权重:"
        echo "  python export_weights.py best_state.pth mortal_large.pth"
        echo "  cp mortal_large.pth mortal.pth"
        echo "  # 更新 config.toml 中 resnet 参数匹配训练配置"
        ;;
esac
