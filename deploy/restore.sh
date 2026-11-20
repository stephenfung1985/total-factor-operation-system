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
if [ -f "$1.password_key" ]; then
  key=$(tr -d '\r\n' < "$1.password_key")
  if [ -n "$key" ]; then
    tmp=$(mktemp)
    grep -v '^TFOS_PASSWORD_KEY=' "$DATA_DIR/env" > "$tmp" || true
    printf 'TFOS_PASSWORD_KEY=%s\n' "$key" >> "$tmp"
    mv "$tmp" "$DATA_DIR/env"
    chmod 600 "$DATA_DIR/env"
    printf '%s\n' "$key" > "${TFOS_DB}.password_key"
    chmod 600 "${TFOS_DB}.password_key"
  fi
fi
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.tfos.server.plist"
echo "已恢复：$1"
