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
    ('admin', 'admin', '无业务数据'), ('fengxuecheng', 'finance', '全部'), ('jinliuchao', 'finance', '全部'),
    ('70152', 'team', '70152 钻井队'), ('gcjsb', 'dept', '工程技术部归口科目')])
def test_initial_accounts(client, username, role, scope):
    r = login(client, username).json()
    u = r['user']
    assert u['role'] == role and u['scope_label'] == scope and u['must_change'] == 1
    me = client.get('/api/me', headers=bearer(r['token'])).json()
    assert me['username'] == username and 'password_hash' not in me


def test_team_70152_scope(client):
    tok = login(client, '70152').json()['token']
    me = client.get('/api/me', headers=bearer(tok)).json()
    assert me['team_id'] == 'T70152'
    assert client.get('/api/reports/wells', headers=bearer(tok)).json() == []      # 新队伍，暂无井
    gc = login(client, 'gcjsb').json()['token']
    assert client.get('/api/me', headers=bearer(gc)).json()['dept'] == '技术'


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


def test_admin_manages_accounts(client):
    adm = bearer(login(client, 'admin').json()['token'])
    fin = bearer(login(client, 'fengxuecheng').json()['token'])
    assert client.get('/api/admin/users', headers=fin).status_code == 403
    users = {u['username']: u for u in client.get('/api/admin/users', headers=adm).json()['users']}
    assert {'admin', 'fengxuecheng', 'jinliuchao', '70152', 'gcjsb'} <= set(users)
    assert all('password_hash' not in u for u in users.values())
    u = client.post('/api/admin/users', json={'username': 'aqhbb', 'role': 'dept', 'scope': '安全', 'password': 'Init8888'},
                    headers=adm).json()
    assert u['scope_label'] == '安全环保部归口科目'
    assert client.post('/api/admin/users', json={'username': 'aqhbb', 'role': 'dept', 'scope': '安全', 'password': 'x'},
                       headers=adm).status_code == 400
    # 停用后会话失效、不能登录；重置密码后须修改
    assert client.post('/api/admin/users/fengxuecheng/enable', json={'enabled': False}, headers=adm).status_code == 200
    assert client.get('/api/me', headers=fin).status_code == 401
    assert '停用' in login(client, 'fengxuecheng').json()['detail']
    client.post('/api/admin/users/fengxuecheng/enable', json={'enabled': True}, headers=adm)
    client.post('/api/admin/users/fengxuecheng/reset', json={'password': 'Temp0001'}, headers=adm)
    assert login(client, 'fengxuecheng', 'Temp0001').json()['user']['must_change'] == 1
    # 不能停用自己
    assert client.post('/api/admin/users/admin/enable', json={'enabled': False}, headers=adm).status_code == 400


def test_passwords_are_hashed(client):
    from app.db import connect
    conn = connect()
    stored = [r[0] for r in conn.execute('SELECT password_hash FROM app_user WHERE username IS NOT NULL')]
    conn.close()
    assert stored and all(h.startswith('pbkdf2_sha256$') and INIT not in h for h in stored)


# ---------- 调试账号 ----------

def test_debug_account_switches_demo_identities(client):
    r = login(client, 'test', 'test').json()
    h = bearer(r['token'])
    me = r['user']
    assert me['debug'] and me['user_id'] == 'u_fin' and me['must_change'] == 0 and me['login_username'] == 'test'
    assert [i['user_id'] for i in me['identities']] == ['u_fin', 'u_team', 'u_dept', 'u_admin']
    assert '[调试 test]' in me['who']
    # 切到基层钻井队：数据范围是 70101 队
    me = client.post('/api/auth/switch', json={'user_id': 'u_team'}, headers=h).json()
    assert me['role'] == 'team' and me['team_id'] == 't1'
    assert [w['well_id'] for w in client.get('/api/reports/wells', headers=h).json()] == ['W001']
    me = client.post('/api/auth/switch', json={'user_id': 'u_admin'}, headers=h).json()
    assert me['role'] == 'admin' and client.get('/api/overview?period=2026-08', headers=h).status_code == 403
    assert client.post('/api/auth/switch', json={'user_id': 'admin'}, headers=h).status_code == 400   # 只能切演示身份
    # 普通账号不能切换
    fin = bearer(login(client, 'fengxuecheng').json()['token'])
    assert client.post('/api/auth/switch', json={'user_id': 'u_admin'}, headers=fin).status_code == 400


def test_debug_actions_are_traceable_and_account_can_be_disabled(client):
    h = bearer(login(client, 'test', 'test').json()['token'])
    client.post('/api/auth/switch', json={'user_id': 'u_dept'}, headers=h)
    rid = client.get('/api/review/reports', headers=h).json()[0]['report_id']
    out = client.post(f'/api/reports/{rid}/approve', json={}, headers=h).json()
    assert out['history'][-1]['operator'].endswith('[调试 test]')
    # 调试账号改密码改的是 test 自己
    assert client.post('/api/auth/password', json={'old_password': 'test', 'new_password': 'debug1234'}, headers=h).status_code == 200
    assert login(client, 'test', 'debug1234').status_code == 200
    adm = bearer(login(client, 'admin').json()['token'])
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
    assert login(c, 'admin', INIT).status_code == 400          # 默认密码不可用
    assert login(c, 'admin', 'Abcd1234x').status_code == 200   # 环境变量给定的初始密码
    assert login(c, 'test', 'test').status_code == 400         # 调试账号不存在或已停用
