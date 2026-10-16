"""账号、密码与登录会话。

密码以 PBKDF2-SHA256 加盐存储，不保存明文；初始密码登录后必须修改。
连续输错 5 次锁定 15 分钟。会话令牌随机生成，12 小时有效，退出即失效。
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta

from .db import now

ITERATIONS = 200_000
SESSION_HOURS = 12
MAX_FAILED = 5
LOCK_MINUTES = 15
INITIAL_PASSWORD = '123456'
ROLE_WHO = {'finance': '企划财务部'}

# 初始账号：(登录名, 角色, 队号)。专业部室角色已取消：部室只提供规则变化依据，由管理层维护规则。
INITIAL_ACCOUNTS = [
    ('fengxuecheng', 'finance', None),
    ('jinliuchao', 'finance', None),
    ('70152', 'team', '70152'),
]
PUBLIC_FIELDS = ('user_id', 'username', 'name', 'role', 'who', 'team_id', 'dept', 'scope_label', 'must_change', 'enabled',
                 'last_login', 'created_at', 'is_debug')

# 调试账号：登录后可在演示身份间切换，用于系统调试；正式上线前由系统管理员停用
DEBUG_USERNAME = 'test'
DEBUG_PASSWORD = 'test'
DEMO_USERS = [   # (user_id, 姓名, 角色, 身份说明, 队伍, 部室, 数据范围)
    ('u_fin', '经营管理员', 'finance', '企划财务部 · 经营管理', None, None, '全部'),
    ('u_team', '统计员', 'team', '70101 钻井队 · 统计员', 't1', None, '70101 钻井队'),
]
DEMO_IDS = [u[0] for u in DEMO_USERS]


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, it, salt, digest = (stored or '').split('$')
        if algo != 'pbkdf2_sha256':
            return False
        calc = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), digest)
    except ValueError:
        return False


def check_strength(password: str) -> None:
    if len(password) < 8 or not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise AuthError('新密码至少 8 位，须同时包含字母和数字')


def public(user: dict) -> dict:
    return {k: user.get(k) for k in PUBLIC_FIELDS}


def _profile(conn, role: str, username: str, scope: str | None) -> dict:
    """按角色生成显示名、身份说明与数据范围；基层队按队号挂队伍（不存在则建档）。"""
    if role == 'team':
        team = conn.execute('SELECT * FROM team WHERE team_no=?', (scope,)).fetchone()
        if not team:
            tid = 'T' + scope
            conn.execute('INSERT INTO team (team_id, team_no, name) VALUES (?,?,?)', (tid, scope, f'{scope} 钻井队'))
            team = conn.execute('SELECT * FROM team WHERE team_id=?', (tid,)).fetchone()
        return {'who': f"{team['name']} · {username}", 'team_id': team['team_id'], 'dept': None, 'scope_label': team['name']}
    if role == 'finance':
        return {'who': f'企划财务部 · {username}', 'team_id': None, 'dept': None, 'scope_label': '全部'}
    raise AuthError('角色不正确')


def create_user(conn, username: str, role: str, scope: str | None, password: str, name: str | None = None,
                must_change: bool = True) -> dict:
    username = (username or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{2,32}', username):
        raise AuthError('登录名为 2–32 位字母、数字、下划线、点或短横线')
    if conn.execute('SELECT 1 FROM app_user WHERE username=? OR user_id=?', (username, username)).fetchone():
        raise AuthError(f'登录名 {username} 已存在')
    if not password:
        raise AuthError('请设置初始密码')
    p = _profile(conn, role, username, (scope or '').strip() or None)
    conn.execute(
        'INSERT INTO app_user (user_id, name, role, who, team_id, dept, scope_label, username, password_hash, must_change, '
        'enabled, failed_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,0,?)',
        (username, (name or '').strip() or username, role, p['who'], p['team_id'], p['dept'], p['scope_label'], username,
         hash_password(password), 1 if must_change else 0, now()))
    return public(dict(conn.execute('SELECT * FROM app_user WHERE user_id=?', (username,)).fetchone()))


def is_production() -> bool:
    """TFOS_PRODUCTION=1 为对外服务模式：不建调试账号、停用已有调试账号、初始密码必须由环境变量给出、禁用演示认证头。"""
    return os.environ.get('TFOS_PRODUCTION') == '1'


def production_check() -> str:
    """对外服务模式下返回初始密码；配置不合格时抛出 RuntimeError，服务拒绝启动。"""
    if os.environ.get('TFOS_DEMO_AUTH') == '1':
        raise RuntimeError('TFOS_PRODUCTION=1 时不得设置 TFOS_DEMO_AUTH（它允许用请求头冒充任何账号）')
    pwd = os.environ.get('TFOS_INITIAL_PASSWORD', '')
    if len(pwd) < 8 or not re.search(r'[A-Za-z]', pwd) or not re.search(r'\d', pwd) or pwd == INITIAL_PASSWORD:
        raise RuntimeError('TFOS_PRODUCTION=1 时必须设置 TFOS_INITIAL_PASSWORD（至少 8 位，含字母和数字，且不能用默认初始密码）')
    return pwd


def _drop_admin_role(conn) -> None:
    """系统管理员角色已取消，功能并入管理层：清除旧库里的管理员账号与演示身份及其会话。"""
    ids = [r['user_id'] for r in conn.execute("SELECT user_id FROM app_user WHERE role='admin'")]
    for uid in ids:
        conn.execute('DELETE FROM app_session WHERE user_id=? OR acting_user_id=?', (uid, uid))
        conn.execute('DELETE FROM app_user WHERE user_id=?', (uid,))


def _drop_dept_role(conn) -> None:
    """专业部室角色已取消（规则与日报不再经部室审核）：清除旧库里的部室账号、演示身份及其会话；历史留痕在操作日志里不受影响。"""
    ids = [r['user_id'] for r in conn.execute("SELECT user_id FROM app_user WHERE role='dept'")]
    for uid in ids:
        conn.execute('DELETE FROM app_session WHERE user_id=? OR acting_user_id=?', (uid, uid))
        conn.execute('DELETE FROM app_user WHERE user_id=?', (uid,))


def ensure_initial_accounts(conn) -> None:
    """建立初始账号、演示身份和调试账号（已存在的不动）。"""
    prod = is_production()
    _drop_admin_role(conn)
    _drop_dept_role(conn)
    initial = production_check() if prod else INITIAL_PASSWORD
    for username, role, scope in INITIAL_ACCOUNTS:
        if not conn.execute('SELECT 1 FROM app_user WHERE username=?', (username,)).fetchone():
            create_user(conn, username, role, scope, initial)
    for uid, name, role, who, team_id, dept, scope in DEMO_USERS:
        if not conn.execute('SELECT 1 FROM app_user WHERE user_id=?', (uid,)).fetchone():
            if team_id and not conn.execute('SELECT 1 FROM team WHERE team_id=?', (team_id,)).fetchone():
                team_id = None
            conn.execute('INSERT INTO app_user (user_id, name, role, who, team_id, dept, scope_label) VALUES (?,?,?,?,?,?,?)',
                         (uid, name, role, who, team_id, dept, scope))
    if prod:
        conn.execute('UPDATE app_user SET enabled=0 WHERE is_debug=1')
        conn.execute('DELETE FROM app_session WHERE user_id IN (SELECT user_id FROM app_user WHERE is_debug=1)')
    elif not conn.execute('SELECT 1 FROM app_user WHERE username=?', (DEBUG_USERNAME,)).fetchone():
        conn.execute(
            'INSERT INTO app_user (user_id, name, role, who, scope_label, username, password_hash, must_change, enabled, failed_count, '
            "created_at, is_debug) VALUES (?, '调试账号', 'finance', '调试账号', '可切换演示身份', ?, ?, 0, 1, 0, ?, 1)",
            (DEBUG_USERNAME, DEBUG_USERNAME, hash_password(DEBUG_PASSWORD), now()))


def login(conn, username: str, password: str) -> tuple[str, dict]:
    u = conn.execute('SELECT * FROM app_user WHERE username=?', ((username or '').strip(),)).fetchone()
    if not u or not u['password_hash']:
        raise AuthError('登录名或密码错误')
    if not u['enabled']:
        raise AuthError('账号已停用，请联系管理层')
    if u['locked_until'] and u['locked_until'] > now():
        raise AuthError(f'密码错误次数过多，账号锁定至 {u["locked_until"][11:16]}')
    if not verify_password(password or '', u['password_hash']):
        failed = (u['failed_count'] or 0) + 1
        locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime('%Y-%m-%d %H:%M:%S') if failed >= MAX_FAILED else None
        conn.execute('UPDATE app_user SET failed_count=?, locked_until=? WHERE user_id=?', (0 if locked else failed, locked, u['user_id']))
        conn.commit()   # 失败计数必须保存，不随本次请求的异常回滚
        raise AuthError(f'登录名或密码错误；连续输错 {MAX_FAILED} 次将锁定 {LOCK_MINUTES} 分钟' if not locked
                        else f'密码错误次数过多，账号锁定 {LOCK_MINUTES} 分钟')
    token = secrets.token_urlsafe(32)
    u = dict(u)
    expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('DELETE FROM app_session WHERE expires_at<?', (now(),))
    conn.execute('INSERT INTO app_session (token_hash, user_id, created_at, expires_at, acting_user_id) VALUES (?,?,?,?,?)',
                 (_token_hash(token), u['user_id'], now(), expires, DEMO_IDS[0] if u['is_debug'] else None))
    conn.execute('UPDATE app_user SET failed_count=0, locked_until=NULL, last_login=? WHERE user_id=?', (now(), u['user_id']))
    return token, public(dict(conn.execute('SELECT * FROM app_user WHERE user_id=?', (u['user_id'],)).fetchone()))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_by_token(conn, token: str) -> dict | None:
    """按令牌取账号。调试账号返回当前切换到的演示身份，并带上登录账号，便于留痕。"""
    r = conn.execute('SELECT u.*, s.acting_user_id FROM app_session s JOIN app_user u ON u.user_id=s.user_id WHERE s.token_hash=? '
                     'AND s.expires_at>? AND u.enabled=1', (_token_hash(token), now())).fetchone()
    if not r:
        return None
    login_user = dict(r)
    if not login_user.get('is_debug'):
        return login_user
    acting = conn.execute('SELECT * FROM app_user WHERE user_id=?', (login_user['acting_user_id'] or DEMO_IDS[0],)).fetchone()
    if not acting:
        return None
    u = dict(acting)
    u.update(who=f"{u['who']} [调试 {login_user['username']}]", login_user_id=login_user['user_id'],
             login_username=login_user['username'], debug=True)
    return u


def switch_identity(conn, token: str, user_id: str) -> None:
    """调试账号切换演示身份。"""
    r = conn.execute('SELECT u.is_debug FROM app_session s JOIN app_user u ON u.user_id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',
                     (_token_hash(token), now())).fetchone()
    if not r or not r['is_debug']:
        raise AuthError('只有调试账号可以切换身份')
    if user_id not in DEMO_IDS or not conn.execute('SELECT 1 FROM app_user WHERE user_id=?', (user_id,)).fetchone():
        raise AuthError('没有这个演示身份')
    conn.execute('UPDATE app_session SET acting_user_id=? WHERE token_hash=?', (user_id, _token_hash(token)))


def demo_identities(conn) -> list[dict]:
    rows = {r['user_id']: dict(r) for r in conn.execute('SELECT user_id, name, role, who, scope_label FROM app_user WHERE user_id IN (%s)'
                                                       % ','.join('?' * len(DEMO_IDS)), DEMO_IDS)}
    return [rows[i] for i in DEMO_IDS if i in rows]


def logout(conn, token: str) -> None:
    conn.execute('DELETE FROM app_session WHERE token_hash=?', (_token_hash(token),))


def change_password(conn, user_id: str, old: str, new: str) -> None:
    u = conn.execute('SELECT * FROM app_user WHERE user_id=?', (user_id,)).fetchone()
    if not u or not verify_password(old or '', u['password_hash']):
        raise AuthError('原密码不正确')
    if old == new:
        raise AuthError('新密码不能与原密码相同')
    check_strength(new or '')
    conn.execute('UPDATE app_user SET password_hash=?, must_change=0 WHERE user_id=?', (hash_password(new), user_id))


def reset_password(conn, user_id: str, password: str) -> None:
    if not password:
        raise AuthError('请输入新的初始密码')
    n = conn.execute('UPDATE app_user SET password_hash=?, must_change=1, failed_count=0, locked_until=NULL WHERE user_id=? '
                     'AND username IS NOT NULL', (hash_password(password), user_id)).rowcount
    if not n:
        raise AuthError('账号不存在')
    conn.execute('DELETE FROM app_session WHERE user_id=?', (user_id,))


def set_enabled(conn, user_id: str, enabled: bool, operator_id: str) -> None:
    if user_id == operator_id and not enabled:
        raise AuthError('不能停用自己的账号')
    if not enabled:
        target = conn.execute('SELECT role FROM app_user WHERE user_id=?', (user_id,)).fetchone()
        others = conn.execute("SELECT COUNT(*) n FROM app_user WHERE role='finance' AND enabled=1 AND username IS NOT NULL "
                              "AND is_debug=0 AND user_id!=?", (user_id,)).fetchone()['n']
        if target and target['role'] == 'finance' and not others:
            raise AuthError('至少保留一个启用的管理层账号')
    conn.execute('UPDATE app_user SET enabled=? WHERE user_id=?', (1 if enabled else 0, user_id))
    if not enabled:
        conn.execute('DELETE FROM app_session WHERE user_id=?', (user_id,))


def list_users(conn) -> list[dict]:
    return [public(dict(r)) for r in conn.execute('SELECT * FROM app_user ORDER BY username IS NULL, role, username')]
