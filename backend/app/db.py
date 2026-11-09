"""SQLite 连接与建库。"""
import json
import os
import re
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
        _heal_dangling_old_foreign_keys(conn)   # 旧迁移把外键改写到已删除的 *_old 表时，在这里改回
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
    ('daily_report', 'ai_task_ids', "TEXT NOT NULL DEFAULT '[]'"),       # 填报 AI 解析任务，提交时落字段留痕
    ('well_info_report', 'ai_task_ids', "TEXT NOT NULL DEFAULT '[]'"),
    ('economic_rule', 'submitted_by', 'TEXT'),    # 普通账户提交人；本列首次补上时，库里已有规则视为已发布
    ('economic_rule', 'submitted_at', 'TEXT'),
]


def _add_columns(conn) -> None:
    added_submit = False
    for table, col, ddl in NEW_COLUMNS:
        have = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
        if col not in have:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
            if table == 'economic_rule' and col == 'submitted_by':
                added_submit = True
    _widen_user_role(conn)
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_username ON app_user (username)')
    if added_submit:
        _publish_legacy_rules(conn)
    _widen_pool_basis(conn)


def _q(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise sqlite3.OperationalError(f'非法表或列名: {name}')
    return f'"{name}"'


# 建表语句开头的表名。sqlite_master 里可能带引号，schema.sql 里带 IF NOT EXISTS。
_CREATE_HEAD = re.compile(
    r'(?is)^(\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)'
    r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)'
)

# SQLite 3.26+ 改写外键后的样子是 REFERENCES "app_user_old"(user_id)，也可能没有引号。
_OLD_FK = re.compile(
    r'(?P<prefix>REFERENCES\s+)'
    r'(?:'
    r'"(?P<dq>[A-Za-z_][A-Za-z0-9_]*)_old"'
    r'|\[(?P<br>[A-Za-z_][A-Za-z0-9_]*)_old\]'
    r'|`(?P<bq>[A-Za-z_][A-Za-z0-9_]*)_old`'
    r'|(?P<bare>[A-Za-z_][A-Za-z0-9_]*)_old\b'
    r')',
    re.IGNORECASE,
)


def _retarget_create(sql: str, new_name: str) -> str:
    match = _CREATE_HEAD.match(sql)
    if not match:
        raise sqlite3.OperationalError(f'无法识别建表语句: {sql[:80]!r}')
    return match.group(1) + _q(new_name) + sql[match.end():]


def _rebuild_tables(conn, specs: list[tuple[str, str]]) -> None:
    """按 SQLite 官方步骤重建表，避免把别的表的外键改写到临时表名上。

    SQLite 3.26 起默认 legacy_alter_table=OFF。`ALTER TABLE X RENAME TO X_old`
    会改写其他表里所有指向 X 的外键（以及触发器、视图里的表名），使它们变成指向
    X_old。随后 DROP X_old，这些外键就悬空。PRAGMA foreign_keys 只控制约束检查，
    并不阻止这次改写；而且这个 PRAGMA 在尚未结束的事务里会被静默忽略。旧写法下，
    同一次启动里 INSERT 已经把事务打开，finally 里的 foreign_keys=ON 实际没生效，
    所以第一次启动的 DELETE 能过；进程重启后新连接打开外键，再执行
    DELETE FROM app_session 就报 no such table: main.app_user_old。

    官方顺序是先建 X_new、拷数据、DROP X、再把 X_new 改回 X。其他表的外键从头到尾
    都写着 X，临时名 X_new 不会出现在别的对象里。不用 legacy_alter_table=ON：那个
    开关把外键、触发器、视图的引用更新一起关掉，连接上一旦忘了关回去，以后正常的
    改名也会留下旧名字。

    foreign_keys 必须在事务外切换。重建放在同一个事务里，提交前跑
    PRAGMA foreign_key_check，不通过就整段回滚。
    specs 是 (现表名, 目标建表语句)，建表语句里的表名仍写现表名。
    """
    if not specs:
        return
    prepared = []
    for table, create_sql in specs:
        new_name = f'{table}_new'
        if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (new_name,)).fetchone():
            raise sqlite3.OperationalError(f'无法重建 {table}：临时表 {new_name} 已存在')
        indexes = [r['sql'] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,))]
        triggers = [r['sql'] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql IS NOT NULL", (table,))]
        old_cols = [r['name'] for r in conn.execute(f'PRAGMA table_info({_q(table)})')]
        prepared.append((table, new_name, _retarget_create(create_sql, new_name), old_cols, indexes, triggers))

    # 先结束隐式事务，否则后面的 PRAGMA foreign_keys 不生效。
    if conn.in_transaction:
        conn.commit()
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        conn.execute('BEGIN')
        for table, new_name, create_new, old_cols, indexes, triggers in prepared:
            conn.execute(create_new)
            new_cols = {r['name'] for r in conn.execute(f'PRAGMA table_info({_q(new_name)})')}
            missing = [c for c in old_cols if c not in new_cols]
            if missing:
                raise sqlite3.OperationalError(f'重建 {table} 时新表缺少列: {", ".join(missing)}')
            if old_cols:
                collist = ', '.join(_q(c) for c in old_cols)
                conn.execute(
                    f'INSERT INTO {_q(new_name)} ({collist}) SELECT {collist} FROM {_q(table)}')
            conn.execute(f'DROP TABLE {_q(table)}')
            conn.execute(f'ALTER TABLE {_q(new_name)} RENAME TO {_q(table)}')
            for sql in indexes:
                conn.execute(sql)
            for sql in triggers:
                conn.execute(sql)
        bad = conn.execute('PRAGMA foreign_key_check').fetchall()
        if bad:
            detail = '; '.join(f'{r[0]} rowid={r[1]} parent={r[2]}' for r in bad)
            raise sqlite3.IntegrityError(f'重建表后外键检查未通过: {detail}')
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.execute('PRAGMA foreign_keys=ON')


def _rewrite_dangling_old_refs(sql: str, live: set[str]) -> str | None:
    """把指向已删除 *_old 表的外键改回去掉后缀的现表。没有这种引用时返回 None。"""
    found = False
    missing: list[str] = []

    def repl(match: re.Match) -> str:
        nonlocal found
        base = match.group('dq') or match.group('br') or match.group('bq') or match.group('bare')
        old = f'{base}_old'
        if old in live:
            return match.group(0)   # 临时表还在，外键仍然有效，不动
        if base not in live:
            missing.append(old)
            return match.group(0)
        found = True
        return f'{match.group("prefix")}{_q(base)}'

    rewritten = _OLD_FK.sub(repl, sql)
    if missing:
        names = ', '.join(sorted(set(missing)))
        raise sqlite3.OperationalError(f'外键指向已删除的临时表 {names}，且没有可改回的现表')
    return rewritten if found else None


def _heal_dangling_old_foreign_keys(conn) -> None:
    """启动时修掉悬空的 *_old 外键：按 sqlite_master 里的建表语句重建该表，行原样保留。

    已用「改名成 *_old 再删除」迁过的库，子表（如 app_session）的 sql 仍写着
    REFERENCES "app_user_old"。这里改回 app_user 后再重建，已经坏掉的环境不用手工修库。
    """
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    live = {r['name'] for r in rows}
    specs = []
    for row in rows:
        fixed = _rewrite_dangling_old_refs(row['sql'], live)
        if fixed is not None:
            specs.append((row['name'], fixed))
    _rebuild_tables(conn, specs)


def _widen_user_role(conn) -> None:
    """旧库的 app_user.role 只允许 finance / team / dept。补上费用管理部室和系统管理员，原有账号原样保留。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='app_user'").fetchone()
    if not row or not row['sql'] or 'cost' in row['sql']:
        return
    info = list(conn.execute('PRAGMA table_info(app_user)'))
    defs = []
    for c in info:
        if c['name'] == 'role':
            defs.append("role TEXT NOT NULL CHECK (role IN ('finance', 'team', 'dept', 'cost', 'admin'))")
            continue
        part = f"{c['name']} {c['type'] or 'TEXT'}"
        if c['pk']:
            part += ' PRIMARY KEY'
        elif c['notnull']:
            part += ' NOT NULL'
        if c['dflt_value'] is not None:
            part += f" DEFAULT {c['dflt_value']}"
        defs.append(part)
    _rebuild_tables(conn, [('app_user', 'CREATE TABLE app_user (' + ', '.join(defs) + ')')])


def _publish_legacy_rules(conn) -> None:
    """升级前已经入库的规则不再重走三级流程：草稿和待审一律视为已发布。已被替代、已废止保持原状。"""
    rows = conn.execute(
        "SELECT rule_id, status FROM economic_rule WHERE status NOT IN ('已发布', '已被替代', '已废止')").fetchall()
    ts = now()
    for r in rows:
        conn.execute(
            "UPDATE economic_rule SET status='已发布', published_at=COALESCE(published_at, ?), "
            'confirmed_at=COALESCE(confirmed_at, ?), confirmed_by=COALESCE(confirmed_by, ?) WHERE rule_id=?',
            (ts, ts, '系统迁移', r['rule_id']))
        log(conn, '规则', r['rule_id'], '迁移', '系统', f"升级前状态为{r['status']}，视为已发布")


def _widen_pool_basis(conn) -> None:
    """旧库的 cost_pool.basis 约束只允许 队天 / 进尺：按新表结构重建，数据原样保留。"""
    r = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cost_pool'").fetchone()
    if not r or "'台月'" in r['sql']:
        return
    ddl = SCHEMA.read_text(encoding='utf-8')
    start = ddl.index('CREATE TABLE IF NOT EXISTS cost_pool')
    create = ddl[start:ddl.index(');', start) + 2]
    _rebuild_tables(conn, [('cost_pool', create)])


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
