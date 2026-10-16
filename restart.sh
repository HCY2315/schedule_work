#!/bin/bash
set -e
cd "$(dirname "$0")"
PORT="${WORKDAY_PORT:-8686}"

pkill -f "[s]erver.py" 2>/dev/null || true
pkill -f "[排]班系统" 2>/dev/null || true
sleep 1

if [ -f server.py ]; then
    setsid env WORKDAY_NO_BROWSER=1 WORKDAY_PORT="$PORT" python3 server.py >/tmp/workday_restart.log 2>&1 </dev/null &
    disown
    MODE="源码模式 server.py"
elif [ -f "dist/排班系统.pyz" ]; then
    setsid env WORKDAY_NO_BROWSER=1 WORKDAY_PORT="$PORT" python3 "dist/排班系统.pyz" >/tmp/workday_restart.log 2>&1 </dev/null &
    disown
    MODE="pyz 模式 dist/排班系统.pyz"
elif [ -f "排班系统" ]; then
    setsid env WORKDAY_NO_BROWSER=1 WORKDAY_PORT="$PORT" "./排班系统" >/tmp/workday_restart.log 2>&1 </dev/null &
    disown
    MODE="单文件可执行 ./排班系统"
else
    echo "未找到 server.py / dist/排班系统.pyz / ./排班系统"
    exit 1
fi

sleep 2
if curl -s --max-time 4 "http://127.0.0.1:${PORT}/api/meta" >/dev/null 2>&1; then
    echo "已重启（${MODE}），服务正常"
else
    echo "已启动（${MODE}），但健康检查未通过，请查看日志: /tmp/workday_restart.log"
fi
echo "访问地址: http://127.0.0.1:${PORT}"
echo "重要: 浏览器请按 Ctrl+Shift+R 强制刷新，避免旧 JS/CSS 缓存"
echo "日志: /tmp/workday_restart.log"
