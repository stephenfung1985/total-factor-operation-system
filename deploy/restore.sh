#!/bin/bash
# 用法：deploy/restore.sh /路径/tfos-xxxx.db   （会先停服务、保留当前库为 .before-restore，再恢复）
set -euo pipefail
[ $# -eq 1 ] && [ -f "$1" ] || { echo "用法：$0 备份文件.db" >&2; exit 1; }
DATA_DIR="${TFOS_DATA_DIR:-$HOME/tfos-data}"
set -a; . "$DATA_DIR/env"; set +a
launchctl bootout "gui/$(id -u)/com.tfos.server" 2>/dev/null || true
sleep 3   # 等服务真正卸掉
[ -f "$TFOS_DB" ] && mv "$TFOS_DB" "$TFOS_DB.before-restore.$(date +%s)"
rm -f "$TFOS_DB-wal" "$TFOS_DB-shm"
cp "$1" "$TFOS_DB"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.tfos.server.plist"
echo "已恢复：$1"
