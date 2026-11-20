"""账号、密码与登录会话。

登录校验仍用 PBKDF2-SHA256 加盐散列。另用服务器密钥加密保存一份当前密码，
供系统管理员在「角色与权限」查看。密钥优先取环境变量 TFOS_PASSWORD_KEY，
否则写在数据库文件旁的「<库文件名>.password_key」。
升级前已经散列、且之后没有登录、修改或重置的密码无法恢复，查看时如实说明，不编造。
明文密码不写入日志。初始密码登录后必须修改。
连续输错 5 次锁定 15 分钟。会话令牌随机生成，12 小时有效，退出即失效。
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import db_path, now

ITERATIONS = 200_000
SESSION_HOURS = 12
MAX_FAILED = 5
LOCK_MINUTES = 15
INITIAL_PASSWORD = '123456'
ROLE_WHO = {'finance': '企划财务部', 'cost': '费用管理部室', 'admin': '系统管理员'}

# 初始账号：(登录名, 角色, 队号)。
# 规则入库三级：管理端（普通账户）提交，归口部室确认或退回本部室规则，系统管理员发布。
# 归口部室见 _ensure_dept_accounts。不再建立费用管理部室登录账号。
INITIAL_ACCOUNTS = [
    ('fengxuecheng', 'finance', None),
    ('jinliuchao', 'finance', None),
    ('admin', 'admin', None),
    ('70152', 'team', '70152'),
]
PUBLIC_FIELDS = ('user_id', 'username', 'name', 'role', 'who', 'team_id', 'dept', 'scope_label', 'must_change', 'enabled',
                 'last_login', 'created_at', 'is_debug')

# 调试账号：登录后可在演示身份间切换，用于系统调试；正式上线前由管理端停用
DEBUG_USERNAME = 'test'
DEBUG_PASSWORD = 'test'
DEMO_USERS = [   # (user_id, 姓名, 角色, 身份说明, 队伍, 部室, 数据范围)
    ('u_fin', '经营管理员', 'finance', '企划财务部 · 经营管理', None, None, '全部'),
    ('u_team', '统计员', 'team', '70101 钻井队 · 统计员', 't1', None, '70101 钻井队'),
    ('u_cost', '费用管理员', 'cost', '费用管理部室 · 规则确认', None, '费用管理部室', '全部'),
    ('u_admin', '系统管理员', 'admin', '系统管理员 · 规则发布', None, None, '全部'),
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


# 加密口令格式：enc1$ + urlsafe-base64(16 字节随机数 ‖ 32 字节 MAC ‖ 密文)
_KEY_CACHE: dict[str, bytes] = {}
UNRECOVERABLE_REASON = (
    '该账号的密码在支持查看之前已经单向散列，之后没有登录、修改或重置，无法恢复当前密码。'
    '请让用户登录一次，或由管理端重置密码后再查看。'
)
UNDECRYPTABLE_REASON = (
    '已保存的密码无法用当前服务器密钥解开。请确认密钥未更换；用户下次成功登录后即可再次查看，或由管理端重置密码。'
)


def without_secrets(user: dict) -> dict:
    """会话里的账号对象不带密码散列和加密口令，避免被接口原样返回。"""
    return {k: v for k, v in user.items() if k not in ('password_hash', 'password_enc')}


def _ensure_password_column(conn) -> None:
    names = set()
    for r in conn.execute('PRAGMA table_info(app_user)'):
        names.add(r['name'] if isinstance(r, sqlite3.Row) else r[1])
    if 'password_enc' not in names:
        conn.execute('ALTER TABLE app_user ADD COLUMN password_enc TEXT')


def _conn_db_path(conn) -> Path | None:
    row = conn.execute('PRAGMA database_list').fetchone()
    if not row:
        return None
    file = row['file'] if isinstance(row, sqlite3.Row) else row[2]
    return Path(file) if file else None


def _key_material(conn=None) -> tuple[str, bytes, bool]:
    """返回 (缓存键, 密钥材料, 是否按口令慢推导)。环境变量优先于库旁密钥文件。"""
    env = os.environ.get('TFOS_PASSWORD_KEY', '').strip()
    if env:
        return 'env:' + hashlib.sha256(env.encode()).hexdigest(), env.encode(), True
    path = _conn_db_path(conn) if conn is not None else None
    if path is None:
        path = db_path()
    key_path = Path(str(path) + '.password_key')
    material = _read_or_create_key_file(key_path)
    return 'file:' + str(key_path.resolve()) + ':' + hashlib.sha256(material).hexdigest()[:16], material, False


def _read_or_create_key_file(path: Path) -> bytes:
    if path.exists():
        material = path.read_text(encoding='ascii').strip().encode()
        if len(material) < 32:
            raise RuntimeError(f'密码密钥文件不可用：{path}')
        return material
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = secrets.token_hex(32).encode()
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        material = path.read_text(encoding='ascii').strip().encode()
        if len(material) < 32:
            raise RuntimeError(f'密码密钥文件不可用：{path}')
        return material
    try:
        os.write(fd, fresh + b'\n')
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return fresh


def _password_key(conn=None) -> bytes:
    cache_id, material, slow = _key_material(conn)
    key = _KEY_CACHE.get(cache_id)
    if key is None:
        if slow:
            key = hashlib.pbkdf2_hmac('sha256', material, b'tfos-password-at-rest-v1', 200_000)
        else:
            key = hashlib.sha256(b'tfos-password-at-rest-v1' + material).digest()
        _KEY_CACHE[cache_id] = key
    return key


def _xor_keystream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    offset = 0
    counter = 0
    while offset < len(data):
        block = hmac.new(key, b'stream1' + nonce + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
        take = min(len(block), len(data) - offset)
        for i in range(take):
            out[offset + i] = data[offset + i] ^ block[i]
        offset += take
        counter += 1
    return bytes(out)


def encrypt_password(password: str, conn=None) -> str:
    """加密当前密码。返回值不含明文。"""
    key = _password_key(conn)
    nonce = secrets.token_bytes(16)
    ct = _xor_keystream(key, nonce, password.encode('utf-8'))
    mac = hmac.new(key, b'enc1' + nonce + ct, hashlib.sha256).digest()
    return 'enc1$' + base64.urlsafe_b64encode(nonce + mac + ct).decode('ascii')


def decrypt_password(stored: str, conn=None) -> str:
    if not stored or not stored.startswith('enc1$'):
        raise ValueError('unsupported')
    try:
        raw = base64.urlsafe_b64decode(stored[5:])
    except Exception as exc:
        raise ValueError('bad-encoding') from exc
    if len(raw) < 48:
        raise ValueError('truncated')
    nonce, mac, ct = raw[:16], raw[16:48], raw[48:]
    key = _password_key(conn)
    expect = hmac.new(key, b'enc1' + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        raise ValueError('mac')
    return _xor_keystream(key, nonce, ct).decode('utf-8')


def _seal(password: str, conn) -> tuple[str, str]:
    """同时生成登录散列和可恢复的加密口令。"""
    _ensure_password_column(conn)
    return hash_password(password), encrypt_password(password, conn)


def _remember_password(conn, user_id: str, password: str) -> None:
    """登录已核对明文后，补上加密口令。已有且能解开成同一密码时不重写。失败不抛出，以免挡住登录。"""
    try:
        _ensure_password_column(conn)
        row = conn.execute('SELECT password_enc FROM app_user WHERE user_id=?', (user_id,)).fetchone()
        existing = (row['password_enc'] if row else None) or ''
        if existing.startswith('enc1$'):
            try:
                if decrypt_password(existing, conn) == password:
                    return
            except (ValueError, RuntimeError):
                pass
        conn.execute('UPDATE app_user SET password_enc=? WHERE user_id=?', (encrypt_password(password, conn), user_id))
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return


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
    if role == 'admin':
        return {'who': f'系统管理员 · {username}', 'team_id': None, 'dept': None, 'scope_label': '全部'}
    if role == 'dept':
        from .depts import SCOPE_NAME, scope_name
        key = (scope or '').strip()
        if key not in SCOPE_NAME:
            raise AuthError('请选择归口：市场、技术、物资、装备、安全、人事、财务、企划')
        return {'who': f'{scope_name(key)} · {username}', 'team_id': None, 'dept': key, 'scope_label': key}
    if role == 'cost':
        raise AuthError('不再单独建立费用管理部室账号，由各归口部室确认本部室规则')
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
    hashed, sealed = _seal(password, conn)
    conn.execute(
        'INSERT INTO app_user (user_id, name, role, who, team_id, dept, scope_label, username, password_hash, password_enc, must_change, '
        'enabled, failed_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,0,?)',
        (username, (name or '').strip() or username, role, p['who'], p['team_id'], p['dept'], p['scope_label'], username,
         hashed, sealed, 1 if must_change else 0, now()))
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


def _drop_login(conn, username: str) -> None:
    """删掉登录账号及其会话。演示身份没有登录名，不从这里走。"""
    rows = list(conn.execute('SELECT user_id FROM app_user WHERE username=? OR user_id=?', (username, username)))
    for row in rows:
        uid = row['user_id']
        conn.execute('DELETE FROM app_session WHERE user_id=? OR acting_user_id=?', (uid, uid))
        conn.execute('DELETE FROM app_user WHERE user_id=?', (uid,))


def _rename_dept(conn, old: str, username: str, scope: str) -> None:
    """把旧的全称全拼账号改成短登录名，密码和“须改密”标志保留。目标已存在时只删旧账号。"""
    from .depts import scope_name
    row = conn.execute('SELECT * FROM app_user WHERE username=? OR user_id=?', (old, old)).fetchone()
    if not row:
        return
    taken = conn.execute(
        'SELECT user_id FROM app_user WHERE (username=? OR user_id=?) AND user_id!=?',
        (username, username, row['user_id'])).fetchone()
    if taken:
        _drop_login(conn, old)
        return
    uid = row['user_id']
    conn.execute('DELETE FROM app_session WHERE user_id=? OR acting_user_id=?', (uid, uid))
    conn.execute(
        'UPDATE app_user SET user_id=?, username=?, name=?, role=?, who=?, team_id=NULL, dept=?, scope_label=? WHERE user_id=?',
        (username, username, scope_name(scope), 'dept', f'{scope_name(scope)} · {username}', scope, scope, uid))


def _drop_cost_logins(conn) -> None:
    """去掉能登录的费用管理部室账号。没有登录名的演示身份保留，调试账号切换不受影响。"""
    rows = list(conn.execute(
        "SELECT username FROM app_user WHERE role='cost' AND username IS NOT NULL AND COALESCE(is_debug, 0)=0"))
    for row in rows:
        _drop_login(conn, row['username'])


def _ensure_dept_accounts(conn, password: str) -> None:
    """短登录名的归口部室账号。旧的全拼账号改名或删除，已有短名不重置密码。"""
    from .depts import ACCOUNTS, RENAMES, RETIRED_USERNAMES, scope_name
    scopes = {username: scope for username, scope in ACCOUNTS}
    for old, new in RENAMES.items():
        _rename_dept(conn, old, new, scopes[new])
    for username in RETIRED_USERNAMES:
        _drop_login(conn, username)
    _drop_cost_logins(conn)
    for username, scope in ACCOUNTS:
        if conn.execute('SELECT 1 FROM app_user WHERE username=? OR user_id=?', (username, username)).fetchone():
            continue
        create_user(conn, username, 'dept', scope, password, name=scope_name(scope))


def ensure_initial_accounts(conn) -> None:
    """建立初始账号、归口部室账号、演示身份和调试账号（已存在的不动）。"""
    prod = is_production()
    initial = production_check() if prod else INITIAL_PASSWORD
    for username, role, scope in INITIAL_ACCOUNTS:
        if not conn.execute('SELECT 1 FROM app_user WHERE username=?', (username,)).fetchone():
            create_user(conn, username, role, scope, initial)
    _ensure_dept_accounts(conn, initial)
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
        hashed, sealed = _seal(DEBUG_PASSWORD, conn)
        conn.execute(
            'INSERT INTO app_user (user_id, name, role, who, scope_label, username, password_hash, password_enc, must_change, enabled, failed_count, '
            "created_at, is_debug) VALUES (?, '调试账号', 'finance', '调试账号', '可切换演示身份', ?, ?, ?, 0, 1, 0, ?, 1)",
            (DEBUG_USERNAME, DEBUG_USERNAME, hashed, sealed, now()))


def login(conn, username: str, password: str) -> tuple[str, dict]:
    u = conn.execute('SELECT * FROM app_user WHERE username=?', ((username or '').strip(),)).fetchone()
    if not u or not u['password_hash']:
        raise AuthError('登录名或密码错误')
    if not u['enabled']:
        raise AuthError('账号已停用，请联系管理端')
    if u['locked_until'] and u['locked_until'] > now():
        raise AuthError(f'密码错误次数过多，账号锁定至 {u["locked_until"][11:16]}')
    if not verify_password(password or '', u['password_hash']):
        failed = (u['failed_count'] or 0) + 1
        locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime('%Y-%m-%d %H:%M:%S') if failed >= MAX_FAILED else None
        conn.execute('UPDATE app_user SET failed_count=?, locked_until=? WHERE user_id=?', (0 if locked else failed, locked, u['user_id']))
        conn.commit()   # 失败计数必须保存，不随本次请求的异常回滚
        raise AuthError(f'登录名或密码错误；连续输错 {MAX_FAILED} 次将锁定 {LOCK_MINUTES} 分钟' if not locked
                        else f'密码错误次数过多，账号锁定 {LOCK_MINUTES} 分钟')
    _remember_password(conn, u['user_id'], password or '')
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
    login_user = without_secrets(dict(r))
    if not login_user.get('is_debug'):
        return login_user
    acting = conn.execute('SELECT * FROM app_user WHERE user_id=?', (login_user['acting_user_id'] or DEMO_IDS[0],)).fetchone()
    if not acting:
        return None
    u = without_secrets(dict(acting))
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
    hashed, sealed = _seal(new, conn)
    conn.execute('UPDATE app_user SET password_hash=?, password_enc=?, must_change=0 WHERE user_id=?', (hashed, sealed, user_id))


def reset_password(conn, user_id: str, password: str) -> None:
    if not password:
        raise AuthError('请输入新的初始密码')
    hashed, sealed = _seal(password, conn)
    n = conn.execute('UPDATE app_user SET password_hash=?, password_enc=?, must_change=1, failed_count=0, locked_until=NULL WHERE user_id=? '
                     'AND username IS NOT NULL', (hashed, sealed, user_id)).rowcount
    if not n:
        raise AuthError('账号不存在')
    conn.execute('DELETE FROM app_session WHERE user_id=?', (user_id,))


def set_enabled(conn, user_id: str, enabled: bool, operator_id: str) -> None:
    if user_id == operator_id and not enabled:
        raise AuthError('不能停用自己的账号')
    if not enabled:
        target = conn.execute('SELECT role FROM app_user WHERE user_id=?', (user_id,)).fetchone()
        guard = {'finance': '管理端', 'admin': '系统管理员'}
        if target and target['role'] in guard:
            others = conn.execute('SELECT COUNT(*) n FROM app_user WHERE role=? AND enabled=1 AND username IS NOT NULL '
                                  'AND is_debug=0 AND user_id!=?', (target['role'], user_id)).fetchone()['n']
            if not others:
                raise AuthError(f"至少保留一个启用的{guard[target['role']]}账号")
    conn.execute('UPDATE app_user SET enabled=? WHERE user_id=?', (1 if enabled else 0, user_id))
    if not enabled:
        conn.execute('DELETE FROM app_session WHERE user_id=?', (user_id,))


def list_users(conn) -> list[dict]:
    return [public(dict(r)) for r in conn.execute('SELECT * FROM app_user ORDER BY username IS NULL, role, username')]


def reveal_password(conn, user_id: str) -> dict:
    """返回当前密码。库里只有旧散列、或密钥对不上时，password 为 None，不编造。"""
    _ensure_password_column(conn)
    u = conn.execute('SELECT user_id, username, password_enc FROM app_user WHERE user_id=?', (user_id,)).fetchone()
    if not u or not u['username']:
        raise AuthError('账号不存在')
    base = {'user_id': u['user_id'], 'username': u['username'], 'recoverable': False, 'password': None, 'reason': None}
    enc = u['password_enc'] or ''
    if not enc.startswith('enc1$'):
        return {**base, 'reason': UNRECOVERABLE_REASON}
    try:
        password = decrypt_password(enc, conn)
    except (ValueError, RuntimeError):
        return {**base, 'reason': UNDECRYPTABLE_REASON}
    return {**base, 'recoverable': True, 'password': password}
