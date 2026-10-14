#!/bin/bash
# launchd 的启动入口：读取环境变量文件，启动 uvicorn（只开 1 个 worker：SQLite 为单文件库）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${TFOS_DATA_DIR:-$HOME/tfos-data}"
set -a; . "$DATA_DIR/env"; set +a
exec "$ROOT/.venv/bin/python" -m uvicorn --app-dir "$ROOT/backend" app.main:app \
  --host "${TFOS_HOST:-0.0.0.0}" --port "${TFOS_PORT:-8000}" --workers 1 --proxy-headers
