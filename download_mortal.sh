#!/bin/bash
# 下载 Mortal AI 模型文件
# 用法: bash download_mortal.sh
#
# 文件来源: https://github.com/shinkuan/Akagi (v2 分支)
# 需要: curl, python3

set -e

MORTAL_DIR="$(cd "$(dirname "$0")" && pwd)/mortal"
mkdir -p "$MORTAL_DIR"

echo "🀄 下载 Mortal AI 模型文件"
echo "目标目录: $MORTAL_DIR"
echo ""

# ─── 检测系统和 Python 版本 ─────────────────────

PYTHON=${PYTHON:-python3}
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
echo "Python 版本: $PY_VER"

OS=$(uname -s)
ARCH=$(uname -m)

case "$OS-$ARCH" in
    Linux-x86_64)   PLATFORM="x86_64-unknown-linux-gnu"; EXT="so" ;;
    Darwin-arm64)   PLATFORM="aarch64-apple-darwin"; EXT="so" ;;
    Darwin-x86_64)  PLATFORM="x86_64-apple-darwin"; EXT="so" ;;
    MINGW*|MSYS*|CYGWIN*)
                    PLATFORM="x86_64-pc-windows-msvc"; EXT="pyd" ;;
    *)  echo "❌ 不支持的系统: $OS-$ARCH"
        echo "请手动从 https://github.com/shinkuan/Akagi 下载 libriichi"
        exit 1 ;;
esac

# Python 版本检查
case "$PY_VER" in
    3.10|3.11|3.12) ;;
    *)  echo "⚠️  Mortal 支持 Python 3.10-3.12，当前: $PY_VER"
        echo "   如果下载后无法使用，请切换 Python 版本"
        ;;
esac

LIBRIICHI_NAME="libriichi-${PY_VER}-${PLATFORM}.${EXT}"
echo "libriichi 文件: $LIBRIICHI_NAME"
echo ""

# ─── 下载 mortal.pth ────────────────────────────

PTH_FILE="$MORTAL_DIR/mortal.pth"
PTH_EXPECTED_SIZE=5123618

if [ -f "$PTH_FILE" ] && [ "$(stat -c%s "$PTH_FILE" 2>/dev/null || stat -f%z "$PTH_FILE" 2>/dev/null)" = "$PTH_EXPECTED_SIZE" ]; then
    echo "✅ mortal.pth 已存在 ($(du -h "$PTH_FILE" | cut -f1))"
else
    echo "⬇️  下载 mortal.pth (5MB)..."
    curl -L --connect-timeout 10 --max-time 120 --retry 3 --progress-bar \
        "https://cdn.jsdelivr.net/gh/shinkuan/Akagi@v2/mjai_bot/mortal/mortal.pth" \
        -o "$PTH_FILE"

    ACTUAL_SIZE=$(stat -c%s "$PTH_FILE" 2>/dev/null || stat -f%z "$PTH_FILE" 2>/dev/null)
    if [ "$ACTUAL_SIZE" = "$PTH_EXPECTED_SIZE" ]; then
        echo "✅ mortal.pth 下载完成"
    else
        echo "⚠️  mortal.pth 大小不匹配 (期望 ${PTH_EXPECTED_SIZE}, 实际 ${ACTUAL_SIZE})"
        echo "   可能下载不完整，请重试"
    fi
fi

echo ""

# ─── 下载 libriichi ─────────────────────────────

LIBRIICHI_TARGET="$MORTAL_DIR/libriichi.${EXT}"

if [ -f "$LIBRIICHI_TARGET" ] && [ "$(stat -c%s "$LIBRIICHI_TARGET" 2>/dev/null || stat -f%z "$LIBRIICHI_TARGET" 2>/dev/null)" -gt 1000000 ]; then
    echo "✅ libriichi.${EXT} 已存在 ($(du -h "$LIBRIICHI_TARGET" | cut -f1))"
else
    echo "⬇️  下载 $LIBRIICHI_NAME (~20MB，可能较慢)..."

    # 先获取 blob SHA
    SHA=$(curl -sL --connect-timeout 10 --max-time 30 \
        "https://api.github.com/repos/shinkuan/Akagi/contents/mjai_bot/mortal/libriichi/${LIBRIICHI_NAME}" \
        | $PYTHON -c "import sys,json; print(json.load(sys.stdin)['sha'])" 2>/dev/null)

    if [ -z "$SHA" ]; then
        echo "❌ 找不到 $LIBRIICHI_NAME"
        echo "   请检查 Python 版本 ($PY_VER) 和系统 ($OS-$ARCH) 是否支持"
        echo "   手动下载: https://github.com/shinkuan/Akagi/tree/v2/mjai_bot/mortal/libriichi"
        exit 1
    fi

    echo "   blob SHA: $SHA"
    curl -L --connect-timeout 10 --max-time 900 --retry 3 --progress-bar \
        -H "Accept: application/vnd.github.raw+json" \
        "https://api.github.com/repos/shinkuan/Akagi/git/blobs/${SHA}" \
        -o "$LIBRIICHI_TARGET"

    ACTUAL_SIZE=$(stat -c%s "$LIBRIICHI_TARGET" 2>/dev/null || stat -f%z "$LIBRIICHI_TARGET" 2>/dev/null)
    if [ "$ACTUAL_SIZE" -gt 1000000 ]; then
        echo "✅ libriichi.${EXT} 下载完成 ($(du -h "$LIBRIICHI_TARGET" | cut -f1))"
        chmod +x "$LIBRIICHI_TARGET" 2>/dev/null || true
    else
        echo "❌ libriichi 下载失败或不完整 (${ACTUAL_SIZE} bytes)"
        echo "   请重试，或手动下载:"
        echo "   https://github.com/shinkuan/Akagi/tree/v2/mjai_bot/mortal/libriichi"
        exit 1
    fi
fi

echo ""

# ─── 下载 model.py 等代码文件 ────────────────────

for FILE in model.py bot_mortal.py logger.py ot_settings.json; do
    TARGET="$MORTAL_DIR/$FILE"
    SRC_NAME="$FILE"
    # bot_mortal.py 在源仓库里叫 bot.py
    [ "$FILE" = "bot_mortal.py" ] && SRC_NAME="bot.py"

    if [ -f "$TARGET" ] && [ "$(stat -c%s "$TARGET" 2>/dev/null || stat -f%z "$TARGET" 2>/dev/null)" -gt 50 ]; then
        echo "✅ $FILE 已存在"
    else
        echo "⬇️  下载 $FILE..."
        curl -sL --connect-timeout 10 --max-time 30 \
            "https://api.github.com/repos/shinkuan/Akagi/contents/mjai_bot/mortal/${SRC_NAME}" \
            | $PYTHON -c "import sys,json,base64; d=json.load(sys.stdin); open('${TARGET}','w').write(base64.b64decode(d['content']).decode())"
    fi
done

echo ""

# ─── 验证 ──────────────────────────────────────

echo "─── 验证文件 ───"
echo ""
ls -lh "$MORTAL_DIR"/mortal.pth "$MORTAL_DIR"/libriichi.* "$MORTAL_DIR"/model.py 2>/dev/null

echo ""
echo "─── 测试加载 ───"
echo ""

$PYTHON -c "
import torch, pathlib, sys
pth = pathlib.Path('$MORTAL_DIR/mortal.pth')
state = torch.load(pth, map_location='cpu', weights_only=False)
cfg = state['config']
ver = cfg['control']['version']
print(f'✅ 模型加载成功! 版本={ver}, conv={cfg[\"resnet\"][\"conv_channels\"]}, blocks={cfg[\"resnet\"][\"num_blocks\"]}')
" 2>&1 || echo "⚠️  模型加载测试失败（可能缺少 torch: pip install torch --index-url https://download.pytorch.org/whl/cpu）"

echo ""
echo "=========================================="
echo "🎉 Mortal AI 文件准备完成!"
echo ""
echo "使用方法:"
echo "  1. config.yaml 中设置 ai.type: \"mortal\""
echo "  2. python bot.py"
echo "=========================================="
