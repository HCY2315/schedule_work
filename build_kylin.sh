#!/bin/bash
# 【在麒麟/目标 Linux 上运行】用 PyInstaller 打包成单个可执行文件 dist/排班系统
# 必须在目标机器（相同 CPU 架构与 glibc）上打包，不能跨机器交叉编译
set -e
cd "$(dirname "$0")"

ARGS=(--noconfirm --onefile --name 排班系统 --add-data "static:static" --paths . server.py)

if python3 -c "import PyInstaller" >/dev/null 2>&1; then
    python3 -m PyInstaller "${ARGS[@]}"
else
    echo "未找到 PyInstaller，安装到本地目录（需联网，不改动系统 Python）..."
    rm -rf .pybuild_deps
    python3 -m pip install --quiet --target .pybuild_deps pyinstaller || {
        echo "安装失败；可尝试：python3 -m pip install --user pyinstaller --break-system-packages"
        exit 1
    }
    PYTHONPATH="$(pwd)/.pybuild_deps" python3 -m PyInstaller "${ARGS[@]}"
    rm -rf .pybuild_deps
fi

rm -rf build
rm -f ./*.spec

echo ""
echo "完成: $(pwd)/dist/排班系统"
echo "直接运行: ./dist/排班系统"
echo "首次运行会在该文件同目录生成 data.db 数据库"
echo "如需桌面图标: ./创建桌面图标.sh"
