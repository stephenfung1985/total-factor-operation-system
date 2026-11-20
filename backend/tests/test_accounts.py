import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


INIT = '123456'


def login(client, username, password=INIT):
    return client.post('/api/auth/login', json={'username': username, 'password': password})


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.mark.parametrize('username,role,scope', [
    ('fengxuecheng', 'finance', '全部'), ('jinliuchao', 'finance', '全部'),
    ('admin', 'admin', '全部'), ('70152', 'team', '70152 钻井队')])
def test_initial_accounts(client, username, role, scope):
    r = login(client, username).json()
    u = r['user']
    assert u['role'] == role and u['scope_label'] == scope and u['must_change'] == 1
    me = client.get('/api/me', headers=bearer(r['token'])).json()
    assert me['username'] == username and {'password', 'password_hash', 'password_enc'}.isdisjoint(me)


def test_team_70152_scope(client):
    tok = login(client, '70152').json()['token']
    me = client.get('/api/me', headers=bearer(tok)).json()
    assert me['team_id'] == 'T70152'
    assert client.get('/api/reports/wells', headers=bearer(tok)).json() == []      # 新队伍，暂无井


def test_dept_scope_uses_short_label(client):
    """归口账号按短标记建立。全称不能当作范围，费用管理部室角色不能再新建。"""
    assert login(client, 'gcjsb').status_code == 400
    assert login(client, 'feiyong').status_code == 400
    adm = bearer(login(client, 'jinliuchao').json()['token'])
    assert client.post('/api/admin/users', json={'username': 'anquan2', 'role': 'dept', 'scope': '安全', 'password': 'Init8888'},
                       headers=adm).status_code == 200
    assert client.post('/api/admin/users', json={'username': 'aqhbb', 'role': 'dept', 'scope': '安全环保部', 'password': 'Init8888'},
                       headers=adm).status_code == 400
    assert client.post('/api/admin/users', json={'username': 'cost2', 'role': 'cost', 'password': 'Init8888'},
                       headers=adm).status_code == 400


def test_login_required_and_demo_header_off(client, monkeypatch):
    assert client.get('/api/me').status_code == 401
    assert client.get('/api/me', headers=bearer('bogus')).status_code == 401
    monkeypatch.setenv('TFOS_DEMO_AUTH', '0')
    assert client.get('/api/me', headers={'X-User-Id': 'u_fin'}).status_code == 401


def test_wrong_password_and_lockout(client):
    for _ in range(4):
        r = login(client, 'jinliuchao', 'bad')
        assert r.status_code == 400 and '次数过多' not in r.json()['detail']
    assert '次数过多' in login(client, 'jinliuchao', 'bad').json()['detail']
    r = login(client, 'jinliuchao')                                        # 锁定期内正确密码也不行
    assert r.status_code == 400 and '锁定至' in r.json()['detail']
    assert login(client, 'fengxuecheng').status_code == 200                # 只锁这一个账号


def test_change_password_and_logout(client):
    tok = login(client, 'fengxuecheng').json()['token']
    h = bearer(tok)
    assert client.post('/api/auth/password', json={'old_password': INIT, 'new_password': 'abc'}, headers=h).status_code == 400
    assert client.post('/api/auth/password', json={'old_password': 'x', 'new_password': 'abcd1234'}, headers=h).status_code == 400
    assert client.post('/api/auth/password', json={'old_password': INIT, 'new_password': 'abcd1234'}, headers=h).status_code == 200
    assert client.get('/api/me', headers=h).json()['must_change'] == 0
    assert login(client, 'fengxuecheng').status_code == 400
    tok2 = login(client, 'fengxuecheng', 'abcd1234').json()['token']
    client.post('/api/auth/logout', headers=bearer(tok2))
    assert client.get('/api/me', headers=bearer(tok2)).status_code == 401


def test_management_manages_accounts(client):
    adm = bearer(login(client, 'jinliuchao').json()['token'])
    fin = bearer(login(client, 'fengxuecheng').json()['token'])
    team = bearer(login(client, '70152').json()['token'])
    assert client.get('/api/admin/users', headers=team).status_code == 403
    assert client.get('/api/admin/users', headers=fin).status_code == 200
    users = {u['username']: u for u in client.get('/api/admin/users', headers=adm).json()['users']}
    assert {'fengxuecheng', 'jinliuchao', 'admin', '70152', 'caiwu', 'gongcheng'} <= set(users)
    assert 'feiyong' not in users and 'gcjsb' not in users
    assert users['caiwu']['role'] == 'dept' and users['admin']['role'] == 'admin'
    assert all({'password', 'password_hash', 'password_enc'}.isdisjoint(u) for u in users.values())
    u = client.post('/api/admin/users', json={'username': 'dz70188', 'role': 'team', 'scope': '70188', 'password': 'Init8888'},
                    headers=adm).json()
    assert u['scope_label'] == '70188 钻井队'
    assert client.post('/api/admin/users', json={'username': 'dz70188', 'role': 'team', 'scope': '70188', 'password': 'x'},
                       headers=adm).status_code == 400
    # 停用后会话失效、不能登录；重置密码后须修改
    assert client.post('/api/admin/users/fengxuecheng/enable', json={'enabled': False}, headers=adm).status_code == 200
    assert client.get('/api/me', headers=fin).status_code == 401
    assert '停用' in login(client, 'fengxuecheng').json()['detail']
    client.post('/api/admin/users/fengxuecheng/enable', json={'enabled': True}, headers=adm)
    client.post('/api/admin/users/fengxuecheng/reset', json={'password': 'Temp0001'}, headers=adm)
    assert login(client, 'fengxuecheng', 'Temp0001').json()['user']['must_change'] == 1
    # 不能停用自己；至少保留一个启用的管理端账号
    assert client.post('/api/admin/users/jinliuchao/enable', json={'enabled': False}, headers=adm).status_code == 400
    assert client.post('/api/admin/users/fengxuecheng/enable', json={'enabled': False}, headers=adm).status_code == 200
    assert client.post('/api/admin/users/jinliuchao/enable', json={'enabled': False}, headers=adm).status_code == 400


def test_passwords_are_hashed(client):
    from app.accounts import DEBUG_PASSWORD, decrypt_password
    from app.db import connect
    conn = connect()
    rows = list(conn.execute('SELECT username, password_hash, password_enc FROM app_user WHERE username IS NOT NULL'))
    try:
        assert rows
        for r in rows:
            expect = DEBUG_PASSWORD if r['username'] == 'test' else INIT
            assert r['password_hash'].startswith('pbkdf2_sha256$') and expect not in r['password_hash']
            assert r['password_enc'].startswith('enc1$') and expect not in r['password_enc']
            assert decrypt_password(r['password_enc'], conn) == expect
    finally:
        conn.close()


def _logs(conn):
    return [dict(r) for r in conn.execute('SELECT object_type, object_id, action, operator, comment FROM approval_log')]


def test_admin_reveals_current_password_without_leaking_it(client):
    """系统管理员能看到当前密码；其他角色不行；列表、详情和审计日志都不含明文。"""
    adm = bearer(login(client, 'admin').json()['token'])
    fin = bearer(login(client, 'fengxuecheng').json()['token'])
    cost = bearer(login(client, 'gongcheng').json()['token'])
    team = bearer(login(client, '70152').json()['token'])

    nav = client.get('/api/me', headers=adm).json()['nav']
    assert 'perm' in {i['id'] for g in nav for i in g['items']}
    matrix = client.get('/api/admin/permissions', headers=adm).json()['matrix']
    row = next(m for m in matrix if m['name'] == '查看账号当前密码')
    assert row['cells'] == {'team': '', 'finance': '', 'cost': '', 'admin': '查看', 'dept': ''}

    seen = client.post('/api/admin/users/70152/reveal-password', headers=adm)
    assert seen.status_code == 200
    assert seen.json()['recoverable'] is True and seen.json()['password'] == INIT
    assert {'password_hash', 'password_enc'}.isdisjoint(seen.json())

    assert client.post('/api/auth/password', json={'old_password': INIT, 'new_password': 'OnlyAdmin1'}, headers=team).status_code == 200
    assert login(client, '70152').status_code == 400
    changed = client.post('/api/admin/users/70152/reveal-password', headers=adm).json()
    assert changed['recoverable'] is True and changed['password'] == 'OnlyAdmin1'
    assert login(client, '70152', 'OnlyAdmin1').status_code == 200

    assert client.post('/api/admin/users/70152/reset', json={'password': 'ResetOnly2'}, headers=fin).status_code == 200
    assert client.post('/api/admin/users/70152/reset', json={'password': 'Nope9999'}, headers=adm).status_code == 403
    reset = client.post('/api/admin/users/70152/reveal-password', headers=adm).json()
    assert reset['password'] == 'ResetOnly2'
    relog = login(client, '70152', 'ResetOnly2').json()
    assert relog['user']['must_change'] == 1
    assert login(client, '70152', 'OnlyAdmin1').status_code == 400

    created = client.post('/api/admin/users', json={'username': 'viewer1', 'role': 'finance', 'password': 'BrandNew1'}, headers=fin)
    assert created.status_code == 200 and 'BrandNew1' not in created.text
    assert client.post(f"/api/admin/users/{created.json()['user_id']}/reveal-password", headers=adm).json()['password'] == 'BrandNew1'

    secrets = ('OnlyAdmin1', 'ResetOnly2', 'BrandNew1', 'Nope9999')
    for headers in (adm, fin, cost, bearer(relog['token'])):
        for path in ('/api/admin/users', '/api/admin/permissions', '/api/me'):
            res = client.get(path, headers=headers)
            if res.status_code == 200:
                assert 'enc1$' not in res.text and 'pbkdf2_sha256$' not in res.text
                for secret in secrets:
                    assert secret not in res.text
                body = res.json()
                rows = body['users'] if isinstance(body, dict) and 'users' in body else [body]
                for item in rows:
                    if isinstance(item, dict):
                        assert {'password', 'password_hash', 'password_enc'}.isdisjoint(item)
            else:
                assert res.status_code == 403
                for secret in secrets:
                    assert secret not in res.text

    for headers in (fin, cost, bearer(relog['token'])):
        denied = client.post('/api/admin/users/viewer1/reveal-password', headers=headers)
        assert denied.status_code == 403
        for secret in secrets:
            assert secret not in denied.text
    assert client.post('/api/admin/users/viewer1/reveal-password').status_code == 401
    for uid, status in (('u_fin', 403), ('u_cost', 403), ('u_team', 403), ('u_admin', 200)):
        res = client.post('/api/admin/users/viewer1/reveal-password', headers={'X-User-Id': uid})
        assert res.status_code == status
        if status != 200:
            assert 'BrandNew1' not in res.text

    dbg = bearer(login(client, 'test', 'test').json()['token'])
    assert client.post('/api/admin/users/viewer1/reveal-password', headers=dbg).status_code == 403
    assert client.post('/api/auth/switch', json={'user_id': 'u_admin'}, headers=dbg).status_code == 200
    switched = client.post('/api/admin/users/viewer1/reveal-password', headers=dbg)
    assert switched.status_code == 200 and switched.json()['password'] == 'BrandNew1'
    assert client.post('/api/auth/switch', json={'user_id': 'u_cost'}, headers=dbg).status_code == 200
    assert client.post('/api/admin/users/viewer1/reveal-password', headers=dbg).status_code == 403

    from app.db import connect
    conn = connect()
    try:
        logs = [r for r in _logs(conn) if r['action'] == '查看密码']
        assert logs and all(r['comment'] in ('查看当前密码', '查看当前密码（未能显示）') for r in logs)
        blob = ' '.join(' '.join(str(v) for v in r.values() if v) for r in _logs(conn))
        for secret in secrets:
            assert secret not in blob
        stored = conn.execute("SELECT password_enc FROM app_user WHERE username='viewer1'").fetchone()[0]
        assert stored.startswith('enc1$') and 'BrandNew1' not in stored
    finally:
        conn.close()


def test_legacy_hash_is_not_invented(client):
    """升级前只剩散列的密码不能被编造出来；本人再次登录或改密后，管理员才能看到。"""
    from app.accounts import hash_password
    from app.db import connect
    conn = connect()
    conn.execute('UPDATE app_user SET password_hash=?, password_enc=NULL WHERE username=?', (hash_password('LegacyPwd1'), 'fengxuecheng'))
    conn.execute("UPDATE app_user SET password_enc='enc1$not-a-real-ciphertext' WHERE username=?", ('jinliuchao',))
    conn.commit()
    conn.close()

    adm = bearer(login(client, 'admin').json()['token'])
    missing = client.post('/api/admin/users/fengxuecheng/reveal-password', headers=adm)
    assert missing.status_code == 200
    assert missing.json()['recoverable'] is False and missing.json()['password'] is None
    assert 'LegacyPwd1' not in missing.text and '无法恢复' in missing.json()['reason']
    assert login(client, 'fengxuecheng', 'LegacyPwd1').status_code == 200
    assert client.post('/api/admin/users/fengxuecheng/reveal-password', headers=adm).json()['password'] == 'LegacyPwd1'

    broken = client.post('/api/admin/users/jinliuchao/reveal-password', headers=adm)
    assert broken.json()['recoverable'] is False and broken.json()['password'] is None
    assert '密钥' in broken.json()['reason'] and INIT not in broken.text
    assert login(client, 'jinliuchao').status_code == 200
    assert client.post('/api/admin/users/jinliuchao/reveal-password', headers=adm).json()['password'] == INIT

    fin = bearer(login(client, 'fengxuecheng', 'LegacyPwd1').json()['token'])
    for path in ('/api/me', '/api/admin/users', '/api/admin/permissions'):
        assert 'LegacyPwd1' not in client.get(path, headers=fin).text
        assert 'LegacyPwd1' not in client.get(path, headers=adm).text
    assert 'LegacyPwd1' not in client.post('/api/admin/users/fengxuecheng/reveal-password', headers=fin).text

    conn = connect()
    try:
        blob = ' '.join(' '.join('' if v is None else str(v) for v in r) for r in conn.execute('SELECT * FROM approval_log'))
        assert 'LegacyPwd1' not in blob
        enc = conn.execute("SELECT password_enc FROM app_user WHERE username='fengxuecheng'").fetchone()[0]
        assert enc.startswith('enc1$') and 'LegacyPwd1' not in enc
    finally:
        conn.close()


# ---------- 调试账号 ----------

def test_debug_account_switches_demo_identities(client):
    r = login(client, 'test', 'test').json()
    h = bearer(r['token'])
    me = r['user']
    assert me['debug'] and me['user_id'] == 'u_fin' and me['must_change'] == 0 and me['login_username'] == 'test'
    assert [i['user_id'] for i in me['identities']] == ['u_fin', 'u_team', 'u_cost', 'u_admin']
    assert '[调试 test]' in me['who']
    # 切到填报端：数据范围是 70101 队
    me = client.post('/api/auth/switch', json={'user_id': 'u_team'}, headers=h).json()
    assert me['role'] == 'team' and me['team_id'] == 't1'
    assert [w['well_id'] for w in client.get('/api/reports/wells', headers=h).json()] == ['W001']
    assert client.post('/api/auth/switch', json={'user_id': 'u_admin'}, headers=h).json()['role'] == 'admin'
    assert client.post('/api/auth/switch', json={'user_id': 'nobody'}, headers=h).status_code == 400   # 只能切演示身份
    assert client.post('/api/auth/switch', json={'user_id': 'jinliuchao'}, headers=h).status_code == 400
    # 普通账号不能切换
    fin = bearer(login(client, 'fengxuecheng').json()['token'])
    assert client.post('/api/auth/switch', json={'user_id': 'u_admin'}, headers=fin).status_code == 400


def test_debug_actions_are_traceable_and_account_can_be_disabled(client):
    h = bearer(login(client, 'test', 'test').json()['token'])
    rid = client.get('/api/monitor/reports?tab=effective', headers=h).json()[0]['report_id']
    out = client.post(f'/api/reports/{rid}/check', json={}, headers=h).json()
    assert out['history'][-1]['operator'].endswith('[调试 test]')
    # 调试账号改密码改的是 test 自己
    assert client.post('/api/auth/password', json={'old_password': 'test', 'new_password': 'debug1234'}, headers=h).status_code == 200
    assert login(client, 'test', 'debug1234').status_code == 200
    adm = bearer(login(client, 'jinliuchao').json()['token'])
    users = {u['username']: u for u in client.get('/api/admin/users', headers=adm).json()['users']}
    assert users['test']['is_debug'] == 1
    client.post('/api/admin/users/test/enable', json={'enabled': False}, headers=adm)
    assert client.get('/api/me', headers=h).status_code == 401


def _prod_init(tmp_path, monkeypatch, **env):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'prod.db'))
    monkeypatch.setenv('TFOS_PRODUCTION', '1')
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.db import init_db
    init_db()


def test_production_refuses_demo_auth_and_weak_password(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match='TFOS_DEMO_AUTH'):
        _prod_init(tmp_path, monkeypatch, TFOS_INITIAL_PASSWORD='Abcd1234x')   # conftest 已开启 DEMO_AUTH
    monkeypatch.delenv('TFOS_DEMO_AUTH')
    for weak in ('', '123456', 'abcdefgh', 'short1'):
        with pytest.raises(RuntimeError, match='TFOS_INITIAL_PASSWORD'):
            _prod_init(tmp_path, monkeypatch, TFOS_INITIAL_PASSWORD=weak)


def test_production_mode_accounts(tmp_path, monkeypatch):
    monkeypatch.delenv('TFOS_DEMO_AUTH')
    _prod_init(tmp_path, monkeypatch, TFOS_INITIAL_PASSWORD='Abcd1234x')
    from app.main import app
    c = TestClient(app)
    assert login(c, 'jinliuchao', INIT).status_code == 400          # 默认密码不可用
    assert login(c, 'jinliuchao', 'Abcd1234x').status_code == 200   # 环境变量给定的初始密码
    assert login(c, 'test', 'test').status_code == 400         # 调试账号不存在或已停用


def test_production_bad_config_leaves_no_db_file(tmp_path, monkeypatch):
    monkeypatch.delenv('TFOS_DEMO_AUTH')
    with pytest.raises(RuntimeError):
        _prod_init(tmp_path, monkeypatch, TFOS_INITIAL_PASSWORD='123456')
    assert not (tmp_path / 'prod.db').exists()   # 否则下次启动会被当成已有库而不再写入成本项目树


def test_production_empty_db_and_hardening(tmp_path, monkeypatch):
    monkeypatch.delenv('TFOS_DEMO_AUTH')
    _prod_init(tmp_path, monkeypatch, TFOS_INITIAL_PASSWORD='Abcd1234x')
    from app import main
    from app.db import connect
    with connect() as c:
        n = lambda t: c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        assert n('cost_item') > 50 and n('unit_of_measure') > 0     # 基础字典保留
        assert (n('well'), n('economic_rule'), n('daily_report'), n('economic_event')) == (0, 0, 0, 0)
        assert [r[0] for r in c.execute('SELECT team_id FROM team')] == ['T70152']   # 仅初始账号 70152 自动建档的队伍
    assert main.docs_kwargs() == {'docs_url': None, 'redoc_url': None, 'openapi_url': None}
    r = TestClient(main.app)
    tk = login(r, 'fengxuecheng', 'Abcd1234x').json()['token']
    assert r.get('/api/periods', headers=bearer(tk)).status_code == 200   # 空库不报错


def test_docs_kwargs_empty_outside_production(monkeypatch):
    monkeypatch.delenv('TFOS_PRODUCTION', raising=False)
    from app import main
    assert main.docs_kwargs() == {}


def test_upload_size_limit(client, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'MAX_UPLOAD', 1024)
    tk = login(client, 'fengxuecheng').json()['token']
    big = b'x' * (1024 * 1024 + 2048)
    r = client.post('/api/wellinfo/import', files={'file': ('a.xlsx', big)}, headers=bearer(tk))
    assert r.status_code == 413
    r = client.post('/api/wellinfo/import', files={'file': ('a.xlsx', b'x' * 1500)}, headers=bearer(tk))
    assert r.status_code == 413      # 小于 Content-Length 门槛但超过读取上限
