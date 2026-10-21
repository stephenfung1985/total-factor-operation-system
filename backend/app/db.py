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
    """建库并写入示例数据。reset=True 时删除旧库重建。对外服务模式先自检配置，不合格不碰数据库文件。"""
    from . import accounts
    if accounts.is_production():
        accounts.production_check()
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
        if accounts.is_production():
            if fresh:
                seed.run_empty(conn)   # 对外服务模式不写示例数据
        elif fresh:
            seed.run(conn)
        elif not conn.execute('SELECT 1 FROM well_info_report LIMIT 1').fetchone():
            seed.seed_well_info(conn)
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
    ('economic_event', 'pool_id', 'INTEGER'),
    ('cost_pool', 'scope_market', 'TEXT'),       # 费用池范围：大港 / 长庆 / 西北，空 = 全部
    ('cost_pool', 'prev_rig_months', 'REAL'),    # 上年实际钻机台月
    ('daily_report', 'checked_by', 'TEXT'),     # 管理端抽查确认
    ('daily_report', 'checked_at', 'TEXT'),
    ('economic_rule', 'change_basis', 'TEXT'),  # 发布时登记的变化依据（部室提供的文件、通知）
    ('economic_rule', 'ai_task_id', 'INTEGER'),  # AI 提取的草稿来自哪次解析
    ('source_doc', 'file_name', 'TEXT'),         # 上传的依据文件：原件存 数据目录/docs/<指纹>，全文用于 AI 解析与原文定位
    ('source_doc', 'file_path', 'TEXT'),
    ('source_doc', 'full_text', 'TEXT'),
    ('source_doc', 'page_count', 'INTEGER'),
    ('source_doc', 'valid_from', 'TEXT'),
    ('source_doc', 'uploaded_by', 'TEXT'),
    ('source_doc', 'uploaded_at', 'TEXT'),
    ('material', 'enabled', 'INTEGER NOT NULL DEFAULT 1'),   # 主数据维护：物资停用
]


def _add_columns(conn) -> None:
    for table, col, ddl in NEW_COLUMNS:
        have = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
        if col not in have:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_username ON app_user (username)')
    _widen_pool_basis(conn)


def _widen_pool_basis(conn) -> None:
    """旧库的 cost_pool.basis 约束只允许 队天 / 进尺：按新表结构重建，数据原样保留。"""
    r = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cost_pool'").fetchone()
    if not r or "'台月'" in r['sql']:
        return
    ddl = SCHEMA.read_text(encoding='utf-8')
    start = ddl.index('CREATE TABLE IF NOT EXISTS cost_pool')
    create = ddl[start:ddl.index(');', start) + 2]
    conn.execute('ALTER TABLE cost_pool RENAME TO cost_pool_old')
    conn.execute(create)
    cols = ', '.join(r['name'] for r in conn.execute('PRAGMA table_info(cost_pool_old)'))
    conn.execute(f'INSERT INTO cost_pool ({cols}) SELECT {cols} FROM cost_pool_old')
    conn.execute('DROP TABLE cost_pool_old')


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
