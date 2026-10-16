#!/bin/bash
# 【在麒麟/Linux 桌面运行】创建桌面和程序菜单快捷方式
# 自动优先使用 dist/排班系统（PyInstaller单文件），否则用 dist/排班系统.pyz
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -x "$DIR/dist/排班系统" ]; then
    EXEC="$DIR/dist/排班系统"
elif [ -f "$DIR/dist/排班系统.pyz" ]; then
    EXEC="$(command -v python3) $DIR/dist/排班系统.pyz"
else
    echo "未找到可执行程序，请先运行 ./build_kylin.sh 或 ./build_pyz.sh"
    exit 1
fi

NAME="智能排班系统"
MENU_DIR="$HOME/.local/share/applications"
DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$MENU_DIR"

write_desktop() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=$NAME
Comment=班次排班与统计
Exec=$EXEC
Terminal=false
Categories=Utility;
EOF
    chmod +x "$1"
}

write_desktop "$MENU_DIR/$NAME.desktop"
echo "已创建菜单快捷方式: $MENU_DIR/$NAME.desktop"

if [ -d "$DESKTOP_DIR" ]; then
    write_desktop "$DESKTOP_DIR/$NAME.desktop"
    echo "已创建桌面快捷方式: $DESKTOP_DIR/$NAME.desktop"
    echo "（如桌面提示不受信任/无法打开，右键图标选“允许启动”或“属性→权限→允许执行”）"
fi

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$MENU_DIR" 2>/dev/null || true

echo "停止服务: pkill -f 排班系统"
