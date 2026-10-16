#!/bin/bash
# 【在麒麟系统上运行】用 PyInstaller 打包成单个可执行文件 dist/排班系统
# 依赖：麒麟自带 python3；首次需联网执行 pip install pyinstaller
# 注意：必须在目标麒麟机器（及对应CPU架构）上打包，Mac/其他Linux无法交叉编译
set -e
cd "$(dirname "$0")"

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "未安装 pyinstaller，正在安装（需联网）..."
    python3 -m pip install --user pyinstaller || {
        echo "自动安装失败，请手动安装：python3 -m pip install --user pyinstaller"
        exit 1
    }
fi

python3 -m PyInstaller --noconfirm --onefile --name 排班系统 \
    --add-data "static:static" \
    --paths . server.py

echo ""
echo "完成: $(pwd)/dist/排班系统"
echo "直接运行: ./dist/排班系统"
echo "首次运行会在该文件同目录生成 data.db 数据库"
echo "如需桌面图标: ./创建桌面图标.sh"
