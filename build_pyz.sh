#!/bin/bash
# 打包成单文件：dist/排班系统.pyz（跨平台，本机或麒麟上均可执行）
# 运行方式：python3 排班系统.pyz  或给 .pyz 加执行权限后直接双击（需系统 python3）
set -e
cd "$(dirname "$0")"
rm -rf .build_pyz
mkdir -p .build_pyz dist
cp db.py scheduler.py server.py .build_pyz/
cp -R static .build_pyz/static
printf '# -*- coding: utf-8 -*-\nfrom server import main\nmain()\n' > .build_pyz/__main__.py
rm -f "dist/排班系统.pyz"
python3 -m zipapp .build_pyz -p "/usr/bin/env python3" -o "dist/排班系统.pyz"
chmod +x "dist/排班系统.pyz"
rm -rf .build_pyz
echo "完成: $(pwd)/dist/排班系统.pyz （单文件，拷贝到目标机器直接运行）"
