"""SQLite 连接与建库。"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SCHEMA = APP_DIR / 'schema.sql'
DEFAULT_DB = APP_DIR.parent / 'data' / 'tfos.db'


def db_path() -> Path:
    return Path(os.environ.get('TFOS_DB', DEFAULT_DB))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


@contextmanager
def transaction():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(reset: bool = False) -> None:
    """建库并写入示例数据。reset=True 时删除旧库重建。"""
    path = db_path()
    if reset and path.exists():
        path.unlink()
    fresh = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    from . import seed
    with transaction() as conn:
        conn.executescript(SCHEMA.read_text(encoding='utf-8'))   # 建表语句均为 IF NOT EXISTS，旧库自动补新表
        _add_columns(conn)
        from . import report_lines
        report_lines.seed(conn)
        if fresh:
            seed.run(conn)
        elif not conn.execute('SELECT 1 FROM well_info_report LIMIT 1').fetchone():
            seed.seed_well_info(conn)
        from . import accounts
        accounts.ensure_initial_accounts(conn)


# 旧库补列：(表, 列, 定义)
NEW_COLUMNS = [
    ('well_info_report', 'source', "TEXT NOT NULL DEFAULT '填报'"),
    ('well_info_report', 'return_comment', 'TEXT'),
    ('well_info_report', 'returned_by', 'TEXT'),
    ('well_info_report', 'returned_at', 'TEXT'),
    ('daily_report', 'line_notes', "TEXT NOT NULL DEFAULT '{}'"),
    ('app_user', 'username', 'TEXT'),
    ('app_user', 'password_hash', 'TEXT'),
    ('app_user', 'must_change', 'INTEGER NOT NULL DEFAULT 0'),
    ('app_user', 'enabled', 'INTEGER NOT NULL DEFAULT 1'),
    ('app_user', 'failed_count', 'INTEGER NOT NULL DEFAULT 0'),
    ('app_user', 'locked_until', 'TEXT'),
    ('app_user', 'last_login', 'TEXT'),
    ('app_user', 'created_at', 'TEXT'),
    ('app_user', 'is_debug', 'INTEGER NOT NULL DEFAULT 0'),
    ('app_session', 'acting_user_id', 'TEXT'),
]


def _add_columns(conn) -> None:
    for table, col, ddl in NEW_COLUMNS:
        have = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
        if col not in have:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_username ON app_user (username)')


def now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def money(x) -> int:
    """金额一律保留到整数元：四舍五入（0.5 进位，负数对称）。每笔经济事项取整后入库，汇总只是整数相加，口径处处一致。"""
    return int(Decimal(repr(float(x))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def jload(s, default=None):
    if s is None or s == '':
        return default if default is not None else {}
    return json.loads(s)


def jdump(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def log(conn, object_type: str, object_id, action: str, operator: str, comment: str | None = None) -> None:
    conn.execute(
        'INSERT INTO approval_log (object_type, object_id, action, operator, operated_at, comment) VALUES (?,?,?,?,?,?)',
        (object_type, str(object_id), action, operator, now(), comment))
