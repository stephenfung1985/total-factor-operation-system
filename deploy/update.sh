#!/bin/bash
# 安全更新线上版本：先拿线上数据的副本演练新版本，通过才正式部署；失败则线上服务保持原样。
# 用法：deploy/update.sh            拉取 origin/main → 演练 → 部署（内部调用 install.sh，会再做一次备份）
#       deploy/update.sh --check    只对当前代码做演练，不拉代码、不部署
#
# 演练内容（都在临时目录里的数据库副本上做，不碰线上库）：
#   1. 跑自动化测试（测试自带临时库）
#   2. 用对外服务模式的真实配置，在 127.0.0.1:18000 上用新代码启动副本库（表结构升级、自检都在这一步发生）
#   3. 启动后所有数据表的行数不得减少（会话表除外），库完整性检查须通过
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${TFOS_DATA_DIR:-$HOME/tfos-data}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1
cd "$ROOT"

[ -f "$DATA_DIR/env" ] || { echo "找不到 $DATA_DIR/env，请先按 deploy/README.md 完成首次安装" >&2; exit 1; }
[ -f "$DATA_DIR/tfos.db" ] || { echo "找不到线上数据库 $DATA_DIR/tfos.db" >&2; exit 1; }
command -v sqlite3 >/dev/null || { echo "需要 sqlite3" >&2; exit 1; }

OLD=$(git rev-parse HEAD)
if [ "$CHECK_ONLY" = 0 ]; then
  [ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "部署目录有未提交的改动，请先处理（git status）" >&2; exit 1; }
  [ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "部署目录不在 main 分支，请先 git checkout main" >&2; exit 1; }
  git fetch -q origin main
  if [ "$OLD" = "$(git rev-parse origin/main)" ]; then echo "已经是最新版本（$(git rev-parse --short HEAD)），无需更新。"; exit 0; fi
  echo "本次更新包含："
  git log --oneline "$OLD..origin/main"
  git merge -q --ff-only origin/main
fi

rollback() {
  if [ "$CHECK_ONLY" = 0 ]; then
    git reset -q --hard "$OLD"
    echo "已把代码回退到 $(git rev-parse --short "$OLD")；线上服务和数据没有被改动。" >&2
  fi
}
TMP=$(mktemp -d)
SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "✗ 演练失败：$1" >&2; rollback; exit 1; }

echo "== 安装依赖"
[ -d "$ROOT/.venv" ] || python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -q -r backend/requirements.txt -r backend/requirements-dev.txt || fail "依赖安装失败"

echo "== 1/3 自动化测试"
(cd backend && env -u TFOS_PRODUCTION -u TFOS_DB -u TFOS_INITIAL_PASSWORD "$ROOT/.venv/bin/python" -m pytest -q) || fail "自动化测试未通过"

echo "== 2/3 用线上数据的副本启动新版本"
sqlite3 "$DATA_DIR/tfos.db" ".backup '$TMP/tfos.db'" || fail "无法复制线上库"
count_rows() {
  local t
  for t in $(sqlite3 "$1" "select name from sqlite_master where type='table' and name not like 'sqlite_%' and name <> 'app_session' order by 1"); do
    echo "$t $(sqlite3 "$1" "select count(*) from \"$t\"")"
  done
}
count_rows "$TMP/tfos.db" > "$TMP/before.txt"
(
  set -a; . "$DATA_DIR/env"; set +a
  export TFOS_DB="$TMP/tfos.db" TFOS_HOST=127.0.0.1 TFOS_PORT=18000
  exec "$ROOT/.venv/bin/python" -m uvicorn --app-dir "$ROOT/backend" app.main:app \
    --host 127.0.0.1 --port 18000 --workers 1 > "$TMP/server.log" 2>&1
) &
SERVER_PID=$!
ok=0
for _ in $(seq 1 30); do
  kill -0 "$SERVER_PID" 2>/dev/null || break
  curl -fsS --noproxy '*' -o /dev/null http://127.0.0.1:18000/ 2>/dev/null && { ok=1; break; }
  sleep 1
done
[ "$ok" = 1 ] || { tail -15 "$TMP/server.log" >&2; fail "新版本无法用线上数据启动"; }
code=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/api/overview || true)
[ "$code" = 401 ] || fail "接口自检异常（未登录访问 /api/overview 应返回 401，实际 $code）"
kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; SERVER_PID=""

echo "== 3/3 数据完整性"
sqlite3 "$TMP/tfos.db" "PRAGMA integrity_check;" | grep -qx ok || fail "副本库完整性检查未通过"
count_rows "$TMP/tfos.db" > "$TMP/after.txt"
bad=$(awk 'NR==FNR { b[$1]=$2; next } { a[$1]=$2 } END { for (t in b) if (!(t in a) || a[t] < b[t]) print t " " b[t] " -> " (t in a ? a[t] : "表消失") }' "$TMP/before.txt" "$TMP/after.txt")
[ -z "$bad" ] || { echo "$bad" >&2; fail "新版本启动后有数据表的行数减少或消失"; }
echo "✓ 演练通过（$(wc -l < "$TMP/before.txt" | tr -d ' ') 张表，行数均未减少）"

if [ "$CHECK_ONLY" = 1 ]; then echo "--check 模式：不部署。"; exit 0; fi

echo "== 正式部署"
if ! "$ROOT/deploy/install.sh"; then
  echo "部署脚本报错。数据库在部署前已备份；如需回退代码：git reset --hard $OLD && deploy/install.sh" >&2
  exit 1
fi
echo "更新完成：$(git rev-parse --short "$OLD") → $(git rev-parse --short HEAD)"
echo "如需回退：git reset --hard $OLD && deploy/install.sh（数据异常再用 deploy/restore.sh 恢复备份）"
