"""表重建迁移不得把外键改写到已删除的 *_old 表上。

PR #47（1a10cda）用 ALTER TABLE app_user RENAME TO app_user_old 重建账号表。
SQLite 3.26+ 默认会把 app_session 的外键一起改成 REFERENCES "app_user_old"。
旧表删掉之后，下一次启动执行 DELETE FROM app_session 就报 no such table: main.app_user_old。
"""
import re
import sqlite3

from app.db import SCHEMA, connect, init_db

# PR #47 只改了这一处角色约束；替换回去就是当时的 schema.sql。
_NEW_ROLE = "CHECK (role IN ('finance', 'team', 'dept', 'cost', 'admin'))"
_OLD_ROLE = "CHECK (role IN ('finance', 'team', 'dept'))"

_SESSION_SQL = (
    'SELECT token_hash, user_id, created_at, expires_at, acting_user_id '
    'FROM app_session ORDER BY token_hash')


def _old_refs(conn) -> list[str]:
    return [f"{r['type']} {r['name']}" for r in conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL") if '_old' in r['sql']]


def _session_rows(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(_SESSION_SQL)]


def _pre_pr47_schema() -> str:
    text = SCHEMA.read_text(encoding='utf-8')
    assert text.count(_NEW_ROLE) == 1
    return text.replace(_NEW_ROLE, _OLD_ROLE, 1)


def _break_user_foreign_keys(conn) -> None:
    """按旧迁移的顺序改名再删掉，让 SQLite 自己把子表外键写成 app_user_old。"""
    if conn.in_transaction:
        conn.commit()
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute('BEGIN')
    conn.execute('ALTER TABLE app_user RENAME TO app_user_old')
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='app_user_old'").fetchone()['sql']
    create, n = re.subn(
        r'(?is)^(\s*CREATE\s+TABLE\s+)"app_user_old"', r'\1app_user', sql, count=1)
    if n != 1:
        create, n = re.subn(
            r'(?is)^(\s*CREATE\s+TABLE\s+)app_user_old\b', r'\1app_user', sql, count=1)
    assert n == 1
    conn.execute(create)
    cols = [r['name'] for r in conn.execute('PRAGMA table_info(app_user_old)')]
    collist = ', '.join(f'"{c}"' for c in cols)
    conn.execute(f'INSERT INTO app_user ({collist}) SELECT {collist} FROM app_user_old')
    conn.execute('DROP TABLE app_user_old')
    conn.commit()
    conn.execute('PRAGMA foreign_keys=ON')


def test_pre_pr47_db_survives_two_startups(tmp_path, monkeypatch):
    """PR #47 之前的库连续 init_db 两次：第二次不再因悬空外键起不来，库里也不留 *_old。"""
    db = tmp_path / 'before-47.db'
    monkeypatch.setenv('TFOS_DB', str(db))
    # 事故发生在对外服务模式：第二次启动会 DELETE 调试账号的会话。
    monkeypatch.setenv('TFOS_PRODUCTION', '1')
    monkeypatch.setenv('TFOS_DEMO_AUTH', '0')
    monkeypatch.setenv('TFOS_INITIAL_PASSWORD', 'Abcd1234')

    conn = sqlite3.connect(db)
    conn.executescript(_pre_pr47_schema())
    conn.execute(
        'INSERT INTO app_user (user_id, name, role, who, team_id, dept, scope_label) '
        "VALUES ('keep', '保留', 'finance', '企划财务部 · keep', NULL, NULL, '全部')")
    conn.execute(
        'INSERT INTO app_session (token_hash, user_id, created_at, expires_at) '
        "VALUES ('tok-keep', 'keep', '2026-01-01 00:00:00', '2099-01-01 00:00:00')")
    conn.execute('CREATE INDEX ix_keep_name ON app_user(name)')
    conn.commit()
    conn.close()

    init_db()
    init_db()

    c = connect()
    assert c.execute("SELECT role FROM app_user WHERE user_id='keep'").fetchone()['role'] == 'finance'
    assert c.execute("SELECT token_hash FROM app_session WHERE token_hash='tok-keep'").fetchone()
    user_sql = c.execute("SELECT sql FROM sqlite_master WHERE name='app_user'").fetchone()['sql']
    session_sql = c.execute("SELECT sql FROM sqlite_master WHERE name='app_session'").fetchone()['sql']
    assert 'cost' in user_sql and 'admin' in user_sql
    assert 'app_user_old' not in session_sql and 'REFERENCES app_user' in session_sql.replace('"', '')
    idx = c.execute("SELECT sql FROM sqlite_master WHERE name='ix_keep_name'").fetchone()
    assert idx and 'app_user' in idx['sql'] and '_old' not in idx['sql']
    assert _old_refs(c) == []
    assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    c.close()


def test_dangling_app_user_old_fk_is_repaired(tmp_path, monkeypatch):
    """外键已经指向 app_user_old 的库：init_db 改回 app_user，会话行一行不少。"""
    db = tmp_path / 'broken.db'
    monkeypatch.setenv('TFOS_DB', str(db))
    init_db()

    c = connect()
    user_id = c.execute("SELECT user_id FROM app_user WHERE username='fengxuecheng'").fetchone()['user_id']
    c.execute(
        'INSERT INTO app_session (token_hash, user_id, created_at, expires_at, acting_user_id) '
        'VALUES (?,?,?,?,?)',
        ('sess-1', user_id, '2026-11-01 08:00:00', '2099-01-01 00:00:00', None))
    c.execute(
        'INSERT INTO app_session (token_hash, user_id, created_at, expires_at, acting_user_id) '
        'VALUES (?,?,?,?,?)',
        ('sess-2', user_id, '2026-11-01 09:00:00', '2099-01-01 00:00:00', 'u_fin'))
    c.commit()
    _break_user_foreign_keys(c)
    broken = c.execute("SELECT sql FROM sqlite_master WHERE name='app_session'").fetchone()['sql']
    assert 'REFERENCES "app_user_old"' in broken
    before = _session_rows(c)
    assert len(before) == 2
    c.close()

    init_db()

    c = connect()
    assert _session_rows(c) == before
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name='app_session'").fetchone()['sql']
    assert 'app_user_old' not in sql
    assert 'REFERENCES "app_user"' in sql or 'REFERENCES app_user' in sql
    assert _old_refs(c) == []
    assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    # 线上第二次启动失败的就是这句。
    c.execute('DELETE FROM app_session WHERE user_id IN (SELECT user_id FROM app_user WHERE is_debug=1)')
    assert _session_rows(c) == before
    c.close()


def test_old_cost_pool_rebuild_keeps_rows(tmp_path, monkeypatch):
    """basis 仍只有队天/进尺的费用池：换新结构后原行还在，不留下 cost_pool_old。"""
    db = tmp_path / 'pool.db'
    monkeypatch.setenv('TFOS_DB', str(db))
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text(encoding='utf-8'))
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute('DROP TABLE cost_pool')
    conn.execute(
        "CREATE TABLE cost_pool ("
        "pool_id INTEGER PRIMARY KEY AUTOINCREMENT, period TEXT NOT NULL, "
        "item_code TEXT NOT NULL REFERENCES cost_item(item_code), "
        "scope_team_id TEXT REFERENCES team(team_id), "
        "basis TEXT NOT NULL CHECK (basis IN ('队天', '进尺')), "
        "amount REAL NOT NULL, note TEXT, "
        "status TEXT NOT NULL CHECK (status IN ('有效', '已作废')), "
        "created_by TEXT NOT NULL, created_at TEXT NOT NULL, voided_by TEXT, voided_at TEXT)")
    conn.execute(
        "INSERT INTO cost_item (item_code, item_name, level, is_leaf, direction, cost_behavior, sort_no) "
        "VALUES ('C1-05', '折旧', 2, 1, '成本', '固定', 1)")
    conn.execute(
        "INSERT INTO cost_pool (period, item_code, basis, amount, status, created_by, created_at) "
        "VALUES ('2024', 'C1-05', '队天', 12.5, '有效', '财务', '2024-01-01 00:00:00')")
    conn.commit()
    conn.close()

    init_db()
    init_db()

    c = connect()
    row = dict(c.execute(
        'SELECT pool_id, period, item_code, basis, amount, status FROM cost_pool').fetchone())
    assert row == {'pool_id': 1, 'period': '2024', 'item_code': 'C1-05', 'basis': '队天',
                   'amount': 12.5, 'status': '有效'}
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name='cost_pool'").fetchone()['sql']
    assert '台月' in sql and 'cost_pool_old' not in sql
    c.execute(
        "INSERT INTO cost_pool (period, item_code, basis, amount, status, created_by, created_at) "
        "VALUES ('2025', 'C1-05', '台月', 1, '有效', '财务', '2025-01-01 00:00:00')")
    assert c.execute('SELECT pool_id FROM cost_pool WHERE period=?', ('2025',)).fetchone()['pool_id'] == 2
    assert _old_refs(c) == []
    assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    c.close()
