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
    ('70152', 'team', '70152 钻井队')])
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


def test_dept_role_removed(client):
    """专业部室角色已取消：没有部室初始账号，不能新建部室账号，旧库里的部室账号启动时清除。"""
    assert login(client, 'gcjsb').status_code == 400
    adm = bearer(login(client, 'jinliuchao').json()['token'])
    r = client.post('/api/admin/users', json={'username': 'aqhbb', 'role': 'dept', 'scope': '安全', 'password': 'Init8888'}, headers=adm)
    assert r.status_code == 400
    from app.db import connect, init_db
    c = connect()
    c.execute("INSERT INTO app_user (user_id, name, role, who, dept, scope_label, username) VALUES "
              "('old_dept', '旧部室', 'dept', '工程技术部', '技术', '归口', 'old_dept')")
    c.commit()
    c.close()
    init_db()
    c = connect()
    assert not c.execute("SELECT 1 FROM app_user WHERE role='dept'").fetchone()
    c.close()


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
    assert {'fengxuecheng', 'jinliuchao', '70152'} <= set(users) and 'admin' not in users and 'gcjsb' not in users
    assert all('password_hash' not in u for u in users.values())
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
    assert [i['user_id'] for i in me['identities']] == ['u_fin', 'u_team']
    assert '[调试 test]' in me['who']
    # 切到填报端：数据范围是 70101 队
    me = client.post('/api/auth/switch', json={'user_id': 'u_team'}, headers=h).json()
    assert me['role'] == 'team' and me['team_id'] == 't1'
    assert [w['well_id'] for w in client.get('/api/reports/wells', headers=h).json()] == ['W001']
    assert client.post('/api/auth/switch', json={'user_id': 'u_admin'}, headers=h).status_code == 400   # 只能切演示身份
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
