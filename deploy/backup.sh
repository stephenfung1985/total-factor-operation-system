#!/bin/bash
# 热备份数据库（sqlite 的 .backup，写入过程中也安全），保留最近 30 份。
# 备份目录默认 ~/tfos-data/backups；建议把 TFOS_BACKUP_DIR 指到外接硬盘或网盘同步目录。
set -euo pipefail
DATA_DIR="${TFOS_DATA_DIR:-$HOME/tfos-data}"
set -a; . "$DATA_DIR/env"; set +a
DEST="${TFOS_BACKUP_DIR:-$DATA_DIR/backups}"
mkdir -p "$DEST"
OUT="$DEST/tfos-$(date +%Y%m%d-%H%M%S).db"
sqlite3 "$TFOS_DB" ".backup '$OUT'"
sqlite3 "$OUT" "PRAGMA integrity_check;" | grep -qx ok || { echo "备份校验失败：$OUT" >&2; exit 1; }
ls -1t "$DEST"/tfos-*.db | tail -n +31 | xargs -I{} rm -f {}
echo "备份完成：$OUT"
