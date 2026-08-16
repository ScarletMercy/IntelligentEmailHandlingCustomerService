#!/bin/bash
# 打包 AI Email Handler 为单文件可执行程序。
# 入口必须是 cli.py（ai_email:main）：它实现 setup/run/daemon/status 子命令并
# 加载 ~/.ai-email/.env。打包 workflow.py 会绕过配置加载，因缺环境
# 变量直接 RuntimeError（#5）。
set -euo pipefail

# 1. 按 uv.lock 同步环境（含 dev 组：pyinstaller 已在 dependency-groups 中）
uv sync

# 2. 打包 CLI 入口
#    前台运行：./AIHandleQQEmail（等价 `ai-email` 无参数 → cmd_run）
#    说明：daemon 后台模式需派生 `python -m ai_email _worker` 子进程，PyInstaller
#    单文件下 sys.executable 不是解释器，daemon 子命令不可用；后台运行请用
#    Docker 或 systemd。
uv run pyinstaller --onefile --name AIHandleQQEmail ai_email/cli.py

# 3. 安装到系统目录（可选：无权限/无 sudo 时保留产物在 dist/ 自行部署）
DEST_DIR="${AIEMAIL_INSTALL_DIR:-/opt/AIHandleQQEmail}"
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    SUDO=""
    if [ ! -w "$(dirname "$DEST_DIR")" ]; then
        echo "ℹ️  无 sudo 权限且 $DEST_DIR 不可写，跳过安装（产物: dist/AIHandleQQEmail）"
        exit 0
    fi
fi
$SUDO mkdir -p "$DEST_DIR"
$SUDO cp dist/AIHandleQQEmail "$DEST_DIR/"

echo "✅ Build complete! Executable: dist/AIHandleQQEmail"
echo "   安装位置: $DEST_DIR/AIHandleQQEmail"
echo "   首次使用: $DEST_DIR/AIHandleQQEmail setup"
echo "   前台运行: $DEST_DIR/AIHandleQQEmail"
