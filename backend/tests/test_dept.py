"""归口部室账号：全拼登录名、升级保留、只读本部室、不能确认发布或算钱。"""
import pytest
from fastapi.testclient import TestClient

from test_accounts import INIT, bearer, login

# 费用科目 owner_dept 经 DEPT_FULL 归并后的部室全称 → 全拼登录名。
EXPECTED = [
    ('shichangbu', '市场部'),
    ('gongchengjishubu', '工程技术部'),
    ('wuzizhuangbeibu', '物资装备部'),
    ('anquanhuanbaobu', '安全环保部'),
    ('renliziyuanbu', '人力资源部'),
    ('qihuacaiwubu', '企划财务部'),
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


def test_quanpin_is_stable():
    from app.depts import quanpin
    assert quanpin('工程技术部') == 'gongchengjishubu'
    assert quanpin('物资装备部') == 'wuzizhuangbeibu'
    assert quanpin('市场部') == 'shichangbu'
    assert quanpin('安全环保部') == 'anquanhuanbaobu'
    assert quanpin('人力资源部') == 'renliziyuanbu'
    assert quanpin('企划财务部') == 'qihuacaiwubu'
    assert quanpin('工程技术部') == quanpin('工程技术部')
    assert quanpin('工程技术部') == quanpin('工程技术部').lower()
    assert ' ' not in quanpin('工程技术部') and '-' not in quanpin('工程技术部')
    # 全拼取的是全称，不是短标记
    assert quanpin('技术') == 'jishu'
    assert quanpin('装备') == 'zhuangbei'


def test_each_department_account_is_created(client):
    from app.db import connect
    from app.depts import department_accounts
    conn = connect()
    assert department_accounts(conn) == EXPECTED
    conn.close()
    adm = bearer(login(client, 'jinliuchao').json()['token'])
    users = {u['username']: u for u in client.get('/api/admin/users', headers=adm).json()['users']}
    assert 'feiyongguanlibushi' not in users
    assert 'jishu' not in users and 'wuzi' not in users and 'zhuangbei' not in users
    assert 'shichanganquan' not in users and 'gebushi' not in users
    for username, dept in EXPECTED:
        u = users[username]
        assert u['role'] == 'dept' and u['dept'] == dept and u['name'] == dept
        assert u['scope_label'] == dept and dept in u['who'] and u['must_change'] == 1
        assert u['enabled'] == 1
    # 原有个人账号和专用账号不动；企划财务部部室账号不替代财务用户
    assert users['fengxuecheng']['role'] == 'finance' and users['fengxuecheng']['enabled'] == 1
    assert users['jinliuchao']['role'] == 'finance'
    assert users['feiyong']['role'] == 'cost' and users['admin']['role'] == 'admin'
    assert users['70152']['role'] == 'team' and users['test']['is_debug'] == 1


def test_dept_login_opens_scoped_pages(client):
    r = login(client, 'gongchengjishubu').json()
    u = r['user']
    assert u['role'] == 'dept' and u['dept'] == '工程技术部' and u['must_change'] == 1
    assert [i['id'] for g in u['nav'] for i in g['items']] == ['masterdata', 'rules', 'basis']
    me = client.get('/api/me', headers=bearer(r['token'])).json()
    assert me['username'] == 'gongchengjishubu' and 'password_hash' not in me


def _h(client, username):
    return bearer(login(client, username).json()['token'])


def _codes(client, headers):
    return {i['item_code'] for i in client.get('/api/master/items', headers=headers).json()}


def test_items_rules_and_results_are_scoped(client):
    tech, equip, hr = _h(client, 'gongchengjishubu'), _h(client, 'wuzizhuangbeibu'), _h(client, 'renliziyuanbu')
    market, safety, fin_dept = _h(client, 'shichangbu'), _h(client, 'anquanhuanbaobu'), _h(client, 'qihuacaiwubu')
    tech_codes = _codes(client, tech)
    assert 'C2-1-02-01' in tech_codes and 'C2-3-04' in tech_codes and 'C2-3-28-02' in tech_codes
    assert 'C1-01-01' not in tech_codes and 'C2-1-04' not in tech_codes and 'C2-3-28-01' not in tech_codes
    equip_codes = _codes(client, equip)
    assert {'C2-1-04', 'C2-1-05-01', 'C2-2-02-01', 'C2-3-28-03', 'C2-3-28-04'} <= equip_codes
    assert 'C2-1-02-01' not in equip_codes and 'C2-3-28-01' not in equip_codes
    hr_codes = _codes(client, hr)
    assert 'C1-01-01' in hr_codes and 'C2-3-18' in hr_codes and 'C2-1-02-01' not in hr_codes
    market_codes, safety_codes = _codes(client, market), _codes(client, safety)
    assert 'R02' in market_codes and 'C2-3-17-01' in market_codes and 'C2-3-17-02' not in market_codes
    assert 'C2-3-17-02' in safety_codes and 'C2-3-17-01' not in safety_codes and 'R02' not in safety_codes
    assert 'C2-3-29' not in safety_codes   # 安全计提的归口是财务，不按名称猜
    fin_codes = _codes(client, fin_dept)
    assert 'C1-02-01' in fin_codes and 'C1-05' in fin_codes and 'C2-3-29' in fin_codes
    assert 'C1-01-01' not in fin_codes
    assert client.get('/api/master/teams', headers=tech).status_code == 403
    assert client.put('/api/master/items/C2-1-02-01', json={'owner_dept': '市场'}, headers=tech).status_code == 403

    from app.depts import covers
    tech_rules = client.get('/api/rules', headers=tech).json()
    assert tech_rules and all(covers('工程技术部', r['owner_dept'], r['item_name']) for r in tech_rules)
    mud = next(r for r in tech_rules if r['item_code'] == 'C2-1-02-01')
    assert client.get(f"/api/rules/{mud['rule_id']}", headers=hr).status_code == 404
    hr_rules = client.get('/api/rules', headers=hr).json()
    assert any(r['item_code'] == 'C1-01-01' for r in hr_rules)
    assert all(r['item_code'] != 'C2-1-02-01' for r in hr_rules)

    hr_amounts = {i['item_code']: i['amount'] for i in client.get('/api/dept/results', headers=hr).json()['items']}
    assert hr_amounts['C1-01-01'] > 0 and 'C2-1-02-01' not in hr_amounts
    tech_amounts = {i['item_code'] for i in client.get('/api/dept/results', headers=tech).json()['items']}
    assert 'C1-01-01' not in tech_amounts and 'C2-1-02-01' in tech_amounts
    fin = _h(client, 'fengxuecheng')
    assert client.get('/api/dept/results', headers=fin).status_code == 403

    own = client.get('/api/wells/W001/items/C1-01-01/events?period=2026-08', headers=hr)
    assert own.status_code == 200 and own.json()['item']['code'] == 'C1-01-01'
    assert client.get('/api/wells/W001/items/C1-01-01/events?period=2026-08', headers=tech).status_code == 404
    event_id = next(e['event_id'] for e in own.json()['events'] if e['status'] == '有效')
    assert client.get(f'/api/events/{event_id}/trace', headers=hr).status_code == 200
    assert client.get(f'/api/events/{event_id}/trace', headers=tech).status_code == 404


def test_dept_cannot_confirm_publish_or_calculate(client):
    tech = _h(client, 'gongchengjishubu')
    rules = client.get('/api/rules', headers=tech).json()
    draft = next(r for r in rules if r['status'] == '待确认')
    published = next(r for r in rules if r['status'] == '已发布')
    assert client.post(f"/api/rules/{draft['rule_id']}/confirm", headers=tech).status_code == 403
    assert client.post(f"/api/rules/{published['rule_id']}/publish",
                       json={'change_basis': '工程技术部通知文件'}, headers=tech).status_code == 403
    assert client.post(f"/api/rules/{published['rule_id']}/impact", headers=tech).status_code == 403
    assert client.post(f"/api/rules/{draft['rule_id']}/submit", headers=tech).status_code == 403
    assert client.post('/api/recalc', headers=tech).status_code == 403
    assert client.get('/api/overview?period=2026-08', headers=tech).status_code == 403
    assert client.get('/api/overview/trend', headers=tech).status_code == 403
    assert client.get('/api/wells/W001/result?period=2026-08', headers=tech).status_code == 403
    assert client.get('/api/monitor/reports', headers=tech).status_code == 403
    assert client.get('/api/ai/status', headers=tech).status_code == 403
    assert client.get('/api/allocation/options', headers=tech).status_code == 403
    assert client.get('/api/cost-detail/options', headers=tech).status_code == 403
    # 费用管理部室和系统管理员的原权限还在
    cost, admin = _h(client, 'feiyong'), _h(client, 'admin')
    assert client.post(f"/api/rules/{draft['rule_id']}/confirm", headers=cost).status_code == 200
    assert client.post(f"/api/rules/{draft['rule_id']}/publish", json={'change_basis': '不应由部室发布'}, headers=cost).status_code == 403
    assert client.post(f"/api/rules/{published['rule_id']}/confirm", headers=admin).status_code == 403


def test_change_basis_is_scoped_and_does_not_write_rules(client):
    tech, hr = _h(client, 'gongchengjishubu'), _h(client, 'renliziyuanbu')
    fin, admin, cost, team = _h(client, 'fengxuecheng'), _h(client, 'admin'), _h(client, 'feiyong'), _h(client, '70152')
    before = len(client.get('/api/rules', headers=fin).json())
    bad = client.post('/api/basis', json={'title': '短'}, headers=tech)
    assert bad.status_code == 422
    assert client.post('/api/basis', json={'title': '市场部价格通知'}, headers=fin).status_code == 403
    made = client.post('/api/basis', json={'title': '工程技术部〔2026〕12号 定向服务价格调整通知', 'note': '自下月起'}, headers=tech)
    assert made.status_code == 200
    body = made.json()
    assert body['dept'] == '工程技术部' and '定向服务' in body['title']
    mine = client.get('/api/basis', headers=tech).json()
    assert [b['basis_id'] for b in mine] == [body['basis_id']]
    assert client.get('/api/basis', headers=hr).json() == []
    assert any(b['basis_id'] == body['basis_id'] for b in client.get('/api/basis', headers=admin).json())
    assert any(b['basis_id'] == body['basis_id'] for b in client.get('/api/basis', headers=fin).json())
    assert client.get('/api/basis', headers=cost).status_code == 403
    assert client.get('/api/basis', headers=team).status_code == 403
    assert len(client.get('/api/rules', headers=fin).json()) == before
    adm = bearer(login(client, 'jinliuchao').json()['token'])
    second = client.post('/api/admin/users', json={'username': 'shichang2', 'role': 'dept', 'scope': '市场部', 'name': '市场部经办',
                                                   'password': 'Init8888'}, headers=adm)
    assert second.status_code == 200 and second.json()['dept'] == '市场部' and second.json()['scope_label'] == '市场部'


def test_upgrade_keeps_dept_accounts_without_duplicating(client):
    from app.db import connect, init_db
    conn = connect()
    before = {
        r['username']: (r['password_hash'], r['role'], r['dept'], r['must_change'])
        for r in conn.execute("SELECT username, password_hash, role, dept, must_change FROM app_user WHERE role='dept'")
    }
    assert set(before) == {u for u, _ in EXPECTED}
    conn.execute("UPDATE app_user SET must_change=0 WHERE username='gongchengjishubu'")
    conn.execute("INSERT INTO app_user (user_id, name, role, who, dept, scope_label, username) VALUES "
                 "('old_dept', '旧部室', 'dept', '工程技术部', '技术', '归口', 'old_dept')")
    conn.commit()
    conn.close()
    init_db()
    init_db()
    conn = connect()
    rows = list(conn.execute("SELECT username, password_hash, role, dept, must_change FROM app_user WHERE role='dept'"))
    conn.close()
    by = {r['username']: r for r in rows}
    assert by['old_dept']['role'] == 'dept'
    assert by['gongchengjishubu']['must_change'] == 0
    for username, (digest, role, dept, _must) in before.items():
        assert by[username]['password_hash'] == digest
        assert by[username]['role'] == role and by[username]['dept'] == dept
        assert sum(1 for r in rows if r['username'] == username) == 1


def test_production_creates_dept_accounts(tmp_path, monkeypatch):
    monkeypatch.delenv('TFOS_DEMO_AUTH', raising=False)
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'prod.db'))
    monkeypatch.setenv('TFOS_PRODUCTION', '1')
    monkeypatch.setenv('TFOS_INITIAL_PASSWORD', 'Abcd1234x')
    from app.db import init_db
    init_db()
    from app.main import app
    c = TestClient(app)
    assert login(c, 'gongchengjishubu', INIT).status_code == 400
    r = login(c, 'gongchengjishubu', 'Abcd1234x')
    assert r.status_code == 200 and r.json()['user']['role'] == 'dept' and r.json()['user']['must_change'] == 1
    assert login(c, 'feiyong', 'Abcd1234x').json()['user']['role'] == 'cost'
    assert login(c, 'feiyongguanlibushi', 'Abcd1234x').status_code == 400
