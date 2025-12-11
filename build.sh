#!/bin/bash
# 1. 创建虚拟环境
python -m venv venv
source venv/bin/activate

# 2. 安装依赖（从 pyproject.toml）
pip install -e .  # 安装当前项目（包括依赖）

# 3. 安装 PyInstaller
pip install pyinstaller

# 4. 打包
pyinstaller --onefile --name AIHandleQQEmail ThinkingInLangGraph.py

sudo cp dist/AIHandleQQEmail /opt/AIHandleQQEmail/
sudo cp .env.example /opt/AIHandleQQEmail/.env

echo "✅ Build complete! Executable: dist/my_service"