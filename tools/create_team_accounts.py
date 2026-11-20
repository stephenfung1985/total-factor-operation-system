"""为所有钻井队建立基层队账号：账号名取队号中的数字位，初始密码同账号，首次登录须改密。

若数据库目录下有 env，且其中有 TFOS_PASSWORD_KEY，会用它加密初始密码，与线上服务保持一致。

用法：python3 tools/create_team_accounts.py 数据库路径            只演练：列出将建立 / 跳过的账号
      python3 tools/create_team_accounts.py 数据库路径 --apply    先备份，再在一个事务里建号

示例队 t1–t4 不建号。去掉字母后账号名重复的队伍（如 50639 与 BH50639），账号归纯数字队号，另一支跳过并列出。
已存在的账号不动，可重复运行。
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from app import accounts  # noqa: E402

DEMO_TEAMS = ('t1', 't2', 't3', 't4')


def _use_server_password_key(db: str) -> None:
    """未设置密钥时，沿用数据库旁边 env 里的 TFOS_PASSWORD_KEY，避免新账号的加密口令对不上服务。"""
    if os.environ.get('TFOS_PASSWORD_KEY', '').strip():
        return
    envf = Path(db).resolve().parent / 'env'
    if not envf.is_file():
        return
    for line in envf.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() == 'TFOS_PASSWORD_KEY' and value.strip():
            os.environ['TFOS_PASSWORD_KEY'] = value.strip().strip('"').strip("'")
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('db')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    teams = [t for t in conn.execute('SELECT team_id, team_no FROM team ORDER BY team_no') if t['team_id'] not in DEMO_TEAMS]
    # 纯数字队号优先占用账号名
    teams.sort(key=lambda t: (not t['team_no'].isdigit(), t['team_no']))
    plan, skipped, existing, used = [], [], [], set()
    for t in teams:
        name = re.sub(r'\D', '', t['team_no'])
        if not name:
            skipped.append((t['team_no'], '队号无数字'))
        elif name in used:
            skipped.append((t['team_no'], f'账号名 {name} 已归另一支队'))
        elif conn.execute('SELECT 1 FROM app_user WHERE username=?', (name,)).fetchone():
            used.add(name)
            existing.append(name)
        else:
            used.add(name)
            plan.append((name, t['team_no']))
    print(f'将建立 {len(plan)} 个，已存在 {len(existing)} 个，跳过 {len(skipped)} 个')
    for s in skipped:
        print('  跳过', *s)
    if not args.apply:
        print('（演练，未改库；加 --apply 执行）')
        return
    shutil.copy(args.db, args.db + '.bak-before-team-accounts')
    _use_server_password_key(args.db)
    with conn:
        for name, team_no in plan:
            accounts.create_user(conn, name, 'team', team_no, name)
    print('已建立', len(plan), '个账号')


main()
