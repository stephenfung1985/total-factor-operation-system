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

# 加载 launchd 服务。卸载后立刻加载、或服务已在运行时再加载，都可能报 "Bootstrap failed: 5: Input/output error"：
# 先卸载并等它真正卸掉，再加载并重试；仍失败但服务已经在运行，就重启它让新代码生效。
loaded() { launchctl print "gui/$UID_/$1" >/dev/null 2>&1; }
load_service() {
  local label="com.tfos.$1" plist="$AGENTS/com.tfos.$1.plist" i out=""
  plutil -lint "$plist" >/dev/null || { echo "$plist 格式不合法" >&2; return 1; }
  if loaded "$label"; then
    launchctl bootout "gui/$UID_/$label" 2>&1 | sed 's/^/  卸载旧服务时提示：/' >&2 || true
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do loaded "$label" || break; sleep 1; done
  for i in 1 2 3 4 5; do
    out=$(launchctl bootstrap "gui/$UID_" "$plist" 2>&1) && return 0
    sleep 2
  done
  if loaded "$label"; then   # 已经在运行：重启即可
    launchctl kickstart -k "gui/$UID_/$label" && { echo "  $label 已在运行，已重启" >&2; return 0; }
  fi
  if launchctl load -w "$plist" 2>/dev/null && loaded "$label"; then return 0; fi   # 旧式加载作最后手段
  echo "  launchctl 报错：$out" >&2
  return 1
}
for svc in server backup ${1:+frpc}; do
  load_service "$svc" || { echo "服务 com.tfos.$svc 加载失败。请确认是在 Mac mini 本机的终端里运行（不是 SSH），并查看上面的错误。" >&2; exit 1; }
done
sleep 3
curl -fsS -o /dev/null "http://127.0.0.1:${TFOS_PORT:-8000}/" && echo "服务已启动：http://127.0.0.1:${TFOS_PORT:-8000}/" \
  || { echo "启动失败，查看 $DATA_DIR/logs/server.log" >&2; exit 1; }
echo "局域网地址：http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 本机IP):${TFOS_PORT:-8000}/"
