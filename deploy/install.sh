#!/bin/bash
# Mac mini 一键部署：建 venv、构建前端、生成配置、注册开机自启服务和每日备份。
# 用法：deploy/install.sh            （可重复执行，用于更新后重新部署）
#       deploy/install.sh frpc "访问密钥:隧道ID"   （另外注册 SakuraFrp 穿透，见 deploy/README.md）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${TFOS_DATA_DIR:-$HOME/tfos-data}"
AGENTS="$HOME/Library/LaunchAgents"
UID_=$(id -u)

command -v python3 >/dev/null || { echo "需要 Python 3.11+（brew install python）" >&2; exit 1; }
command -v npm >/dev/null || { echo "需要 Node.js 20+（brew install node）" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 版本需 3.11 以上" >&2; exit 1; }

mkdir -p "$DATA_DIR/logs" "$DATA_DIR/backups" "$AGENTS"
chmod 700 "$DATA_DIR"
chmod +x "$ROOT"/deploy/*.sh

[ -d "$ROOT/.venv" ] || python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
npm --prefix "$ROOT/frontend" ci
npm --prefix "$ROOT/frontend" run build

if [ ! -f "$DATA_DIR/env" ]; then
  sed "s#__DATA_DIR__#$DATA_DIR#g" "$ROOT/deploy/env.example" > "$DATA_DIR/env"
  chmod 600 "$DATA_DIR/env"
  echo "已生成 $DATA_DIR/env ，请先编辑其中的 TFOS_INITIAL_PASSWORD，然后重新运行本脚本。"
  exit 0
fi
if grep -q '请改成你自己的初始密码' "$DATA_DIR/env"; then
  echo "请先编辑 $DATA_DIR/env 中的 TFOS_INITIAL_PASSWORD。" >&2; exit 1
fi

# 已有库先备份再更新
if [ -f "$DATA_DIR/tfos.db" ]; then "$ROOT/deploy/backup.sh"; fi

render() { sed -e "s#__ROOT__#$ROOT#g" -e "s#__DATA_DIR__#$DATA_DIR#g" -e "s#__FRPC_ARG__#${2:-}#g" "$1" > "$3"; }
render "$ROOT/deploy/com.tfos.server.plist.tpl" "" "$AGENTS/com.tfos.server.plist"
render "$ROOT/deploy/com.tfos.backup.plist.tpl" "" "$AGENTS/com.tfos.backup.plist"
if [ "${1:-}" = "frpc" ]; then
  [ -x "$DATA_DIR/frpc" ] || { echo "请先把 SakuraFrp 的 frpc（macOS arm64）放到 $DATA_DIR/frpc 并 chmod +x" >&2; exit 1; }
  render "$ROOT/deploy/com.tfos.frpc.plist.tpl" "${2:?缺少 访问密钥:隧道ID}" "$AGENTS/com.tfos.frpc.plist"
  chmod 600 "$AGENTS/com.tfos.frpc.plist"
fi

for svc in server backup ${1:+frpc}; do
  launchctl bootout "gui/$UID_/com.tfos.$svc" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_" "$AGENTS/com.tfos.$svc.plist"
done
sleep 3
curl -fsS -o /dev/null "http://127.0.0.1:${TFOS_PORT:-8000}/" && echo "服务已启动：http://127.0.0.1:${TFOS_PORT:-8000}/" \
  || { echo "启动失败，查看 $DATA_DIR/logs/server.log" >&2; exit 1; }
echo "局域网地址：http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 本机IP):${TFOS_PORT:-8000}/"
