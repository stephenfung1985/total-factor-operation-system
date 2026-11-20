"""归口部室短登录名：按短标记分权，本部室确认规则，没有费用管理部室登录账号。"""
import pytest
from fastapi.testclient import TestClient

from test_accounts import INIT, bearer, login

EXPECTED = [
    ('shichang', '市场', '市场'),
    ('gongcheng', '技术', '工程'),
    ('wuzi', '物资', '物资'),
    ('zhuangbei', '装备', '装备'),
    ('anquan', '安全', '安全'),
    ('renli', '人事', '人力'),
    ('qihua', '企划', '企划'),
    ('caiwu', '财务', '财务'),
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


def _h(client, username):
    return bearer(login(client, username).json()['token'])


def _codes(client, headers):
    return {i['item_code'] for i in client.get('/api/master/items', headers=headers).json()}


def test_short_accounts_replace_long_names_and_feiyong(client):
    adm = bearer(login(client, 'jinliuchao').json()['token'])
    users = {u['username']: u for u in client.get('/api/admin/users', headers=adm).json()['users']}
    assert 'feiyong' not in users and 'feiyongguanlibushi' not in users
    assert 'wuzizhuangbeibu' not in users and 'qihuacaiwubu' not in users and 'gongchengjishubu' not in users
    for username, scope, display in EXPECTED:
        u = users[username]
        assert u['role'] == 'dept' and u['dept'] == scope and u['scope_label'] == scope
        assert u['name'] == display and display in u['who'] and u['must_change'] == 1
    assert users['fengxuecheng']['role'] == 'finance' and users['jinliuchao']['role'] == 'finance'
    assert users['admin']['role'] == 'admin' and users['70152']['role'] == 'team'
    assert users['test']['is_debug'] == 1
    assert not any(u['role'] == 'cost' and u['username'] for u in users.values())
    me = login(client, 'gongcheng').json()['user']
    assert me['dept'] == '技术' and [i['id'] for g in me['nav'] for i in g['items']] == ['masterdata', 'rules', 'basis']


def test_items_are_split_by_short_label(client):
    tech, market = _h(client, 'gongcheng'), _h(client, 'shichang')
    goods, equip = _h(client, 'wuzi'), _h(client, 'zhuangbei')
    hr, safety = _h(client, 'renli'), _h(client, 'anquan')
    plan, finance = _h(client, 'qihua'), _h(client, 'caiwu')
    tech_codes = _codes(client, tech)
    assert {'C2-1-02-01', 'C2-3-04', 'C2-3-28-02'} <= tech_codes
    assert 'C1-01-01' not in tech_codes and 'C2-1-04' not in tech_codes and 'C1-02-01' not in tech_codes
    equip_codes, goods_codes = _codes(client, equip), _codes(client, goods)
    assert 'C2-1-04' in goods_codes and 'C2-1-05-01' not in goods_codes and 'C2-2-02-01' not in goods_codes
    assert 'C2-1-05-01' in equip_codes and 'C2-2-02-01' in equip_codes and 'C2-1-04' not in equip_codes
    assert 'C2-3-28-03' in goods_codes and 'C2-3-28-04' in equip_codes
    assert 'C2-3-28-03' not in equip_codes and 'C2-3-28-04' not in goods_codes
    assert 'C1-01-01' in _codes(client, hr) and 'C2-3-18' in _codes(client, hr)
    assert 'C2-3-17-01' in _codes(client, market) and 'C2-3-17-02' not in _codes(client, market) and 'R02' in _codes(client, market)
    assert 'C2-3-17-02' in _codes(client, safety) and 'C2-3-17-01' not in _codes(client, safety) and 'C2-3-29' not in _codes(client, safety)
    assert 'C1-02-01' in _codes(client, finance) and 'C2-3-29' in _codes(client, finance)
    assert 'C1-02-01' not in _codes(client, plan) and 'C2-1-02-01' not in _codes(client, plan)
    assert client.get('/api/master/teams', headers=tech).status_code == 403
    assert client.get('/api/overview?period=2026-08', headers=tech).status_code == 403
    assert client.post('/api/recalc', headers=tech).status_code == 403


def test_confirm_is_scoped_to_the_department(client):
    tech, market = _h(client, 'gongcheng'), _h(client, 'shichang')
    goods, equip = _h(client, 'wuzi'), _h(client, 'zhuangbei')
    plan, finance = _h(client, 'qihua'), _h(client, 'caiwu')
    fin, admin = _h(client, 'fengxuecheng'), _h(client, 'admin')
    rules = client.get('/api/rules', headers=fin).json()
    draft = next(r for r in rules if r['rule_code'] == 'RL-C2-3-04')
    assert draft['status'] == '草稿'
    client.post(f"/api/rules/{draft['rule_id']}/submit", headers=fin)
    rules = client.get('/api/rules', headers=tech).json()
    draft = next(r for r in rules if r['rule_code'] == 'RL-C2-3-04')
    assert client.post(f"/api/rules/{draft['rule_id']}/confirm", headers=market).status_code == 403
    assert client.post(f"/api/rules/{draft['rule_id']}/confirm", headers=fin).status_code == 403
    assert client.post(f"/api/rules/{draft['rule_id']}/confirm", headers=admin).status_code == 403
    confirmed = client.post(f"/api/rules/{draft['rule_id']}/confirm", headers=tech)
    assert confirmed.status_code == 200 and confirmed.json()['rule']['status'] in ('待发布', '待确认')
    assert client.post(f"/api/rules/{draft['rule_id']}/publish", json={'change_basis': '工程通知'}, headers=tech).status_code == 403
    assert 'C2-1-04' not in {r['item_code'] for r in rules}
    # 物资的新版本只能由物资确认，装备和企划都不行；财务的规则企划不能确认
    mud_like = next(r for r in client.get('/api/rules', headers=fin).json() if r['item_code'] == 'C2-1-04')
    nid = client.post(f"/api/rules/{mud_like['rule_id']}/new-version", json={}, headers=fin).json()['rule']['rule_id']
    client.post(f'/api/rules/{nid}/submit', headers=fin)
    assert client.post(f'/api/rules/{nid}/confirm', headers=equip).status_code == 403
    assert client.post(f'/api/rules/{nid}/confirm', headers=plan).status_code == 403
    assert client.post(f'/api/rules/{nid}/confirm', headers=goods).status_code == 200
    fin_rule = next(r for r in client.get('/api/rules', headers=fin).json() if r['item_code'] == 'C1-05')
    fid = client.post(f"/api/rules/{fin_rule['rule_id']}/new-version", json={}, headers=fin).json()['rule']['rule_id']
    client.post(f'/api/rules/{fid}/submit', headers=fin)
    assert client.post(f'/api/rules/{fid}/confirm', headers=plan).status_code == 403
    assert client.post(f'/api/rules/{fid}/confirm', headers=finance).status_code == 200
    assert client.post(f'/api/rules/{fid}/publish', json={'change_basis': '财务通知'}, headers=finance).status_code == 403


def test_upgrade_renames_long_accounts_and_removes_feiyong(client):
    from app.accounts import hash_password
    from app.db import connect, init_db
    conn = connect()
    digest = hash_password('Keep9988')
    conn.execute("UPDATE app_user SET password_hash=?, must_change=0 WHERE username='gongcheng'", (digest,))
    # 先腾出短名，再放回升级前的长名，模拟已经跑过上一版的库
    for username in ('shichang', 'gongcheng', 'wuzi', 'zhuangbei', 'qihua', 'caiwu'):
        conn.execute('DELETE FROM app_user WHERE username=?', (username,))
    conn.execute(
        "INSERT INTO app_user (user_id, name, role, who, dept, scope_label, username, password_hash, must_change, enabled) "
        "VALUES ('shichangbu', '市场部', 'dept', '市场部 · shichangbu', '市场部', '市场部', 'shichangbu', ?, 0, 1)", (digest,))
    conn.execute(
        "INSERT INTO app_user (user_id, name, role, who, dept, scope_label, username, password_hash, must_change, enabled) "
        "VALUES ('wuzizhuangbeibu', '物资装备部', 'dept', '物资装备部', '物资装备部', '物资装备部', 'wuzizhuangbeibu', ?, 1, 1)",
        (digest,))
    conn.execute(
        "INSERT INTO app_user (user_id, name, role, who, dept, scope_label, username, password_hash, must_change, enabled) "
        "VALUES ('feiyong', '费用', 'cost', '费用管理部室 · feiyong', '费用管理部室', '全部', 'feiyong', ?, 1, 1)", (digest,))
    conn.execute("INSERT INTO app_session (token_hash, user_id, created_at, expires_at) VALUES ('dead', 'feiyong', '2026-01-01', '2099-01-01')")
    kept = conn.execute("SELECT password_hash FROM app_user WHERE username='fengxuecheng'").fetchone()[0]
    conn.commit()
    conn.close()
    init_db()
    conn = connect()
    names = {r['username'] for r in conn.execute('SELECT username FROM app_user')}
    assert 'shichangbu' not in names and 'wuzizhuangbeibu' not in names and 'feiyong' not in names
    shichang = conn.execute("SELECT * FROM app_user WHERE username='shichang'").fetchone()
    assert shichang['password_hash'] == digest and shichang['must_change'] == 0
    assert shichang['dept'] == '市场' and shichang['role'] == 'dept'
    assert conn.execute("SELECT COUNT(*) n FROM app_user WHERE username='wuzi'").fetchone()['n'] == 1
    assert conn.execute("SELECT COUNT(*) n FROM app_user WHERE username='zhuangbei'").fetchone()['n'] == 1
    assert conn.execute("SELECT role, dept FROM app_user WHERE username='qihua'").fetchone()['dept'] == '企划'
    assert conn.execute("SELECT role, dept FROM app_user WHERE username='caiwu'").fetchone()['dept'] == '财务'
    assert conn.execute("SELECT password_hash FROM app_user WHERE username='fengxuecheng'").fetchone()[0] == kept
    assert conn.execute("SELECT 1 FROM app_session WHERE user_id='feiyong'").fetchone() is None
    assert conn.execute("SELECT 1 FROM app_user WHERE role='cost' AND username IS NOT NULL").fetchone() is None
    conn.close()
    assert login(client, 'feiyong').status_code == 400
    assert login(client, 'shichang', 'Keep9988').status_code == 200


def test_production_has_dept_accounts_without_feiyong(tmp_path, monkeypatch):
    monkeypatch.delenv('TFOS_DEMO_AUTH', raising=False)
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'prod.db'))
    monkeypatch.setenv('TFOS_PRODUCTION', '1')
    monkeypatch.setenv('TFOS_INITIAL_PASSWORD', 'Abcd1234x')
    from app.db import init_db
    init_db()
    from app.main import app
    c = TestClient(app)
    assert login(c, 'gongcheng', INIT).status_code == 400
    r = login(c, 'gongcheng', 'Abcd1234x')
    assert r.status_code == 200 and r.json()['user']['role'] == 'dept' and r.json()['user']['dept'] == '技术'
    assert login(c, 'caiwu', 'Abcd1234x').json()['user']['dept'] == '财务'
    assert login(c, 'feiyong', 'Abcd1234x').status_code == 400
