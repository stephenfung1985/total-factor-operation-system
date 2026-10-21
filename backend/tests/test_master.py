"""主数据维护：写接口仅管理端、每次修改留痕、已有事实的报量项目不能删除。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


FIN = {'X-User-Id': 'u_fin'}
TEAM = {'X-User-Id': 'u_team'}


def logs(object_id):
    from app.db import rows, transaction
    with transaction() as c:
        return rows(c.execute('SELECT * FROM approval_log WHERE object_type=? AND object_id=? ORDER BY id', ('主数据', object_id)))


def master(client, kind):
    return client.get(f'/api/master/{kind}', headers=FIN).json()


@pytest.mark.parametrize('method,url,body', [
    ('put', '/api/master/teams/t1', {'captain': '张三'}),
    ('post', '/api/master/materials', {'material_code': 'M-9', 'name': '套管', 'unit': '根'}),
    ('put', '/api/master/materials/[物资编码]-01', {'enabled': False}),
    ('post', '/api/master/units', {'unit': '根', 'usage': '套管'}),
    ('put', '/api/master/units/米', {'usage': '进尺'}),
    ('put', '/api/master/report_lines/C2-1-04', {'enabled': False}),
    ('delete', '/api/master/report_lines/C2-1-01-02', None),
    ('put', '/api/master/items/C2-1-04', {'owner_dept': '物资装备部'}),
])
def test_write_requires_finance(client, method, url, body):
    kw = {'headers': TEAM} | ({'json': body} if body is not None else {})
    r = getattr(client, method)(url, **kw)
    assert r.status_code == 403


def test_team_update_logged_and_account_label(client):
    r = client.put('/api/master/teams/t1', headers=FIN, json={'name': '70101 一队', 'captain': '张三', 'rig_type': 'ZJ50D'})
    assert r.status_code == 200 and set(r.json()['changed']) == {'name', 'captain'}
    t = next(x for x in master(client, 'teams') if x['team_id'] == 't1')
    assert t['name'] == '70101 一队' and t['captain'] == '张三'
    (lg,) = logs('队伍:t1')
    assert lg['action'] == '修改' and '队长 [队长] → 张三' in lg['comment'] and lg['operator']
    me = client.get('/api/me', headers=TEAM).json()
    assert me['scope_label'] == '70101 一队' and me['who'].startswith('70101 一队')
    # 没有变化时不留痕
    client.put('/api/master/teams/t1', headers=FIN, json={'captain': '张三'})
    assert len(logs('队伍:t1')) == 1
    assert client.put('/api/master/teams/t1', headers=FIN, json={'name': ' '}).status_code == 422


def test_material_create_update_disable(client):
    r = client.post('/api/master/materials', headers=FIN, json={'material_code': 'M-9', 'name': '套管', 'unit': '根', 'item_code': 'C2-1-04'})
    assert r.status_code == 200
    assert client.post('/api/master/materials', headers=FIN, json={'material_code': 'M-9', 'name': 'x', 'unit': '根'}).status_code == 409
    assert client.post('/api/master/materials', headers=FIN,
                       json={'material_code': 'M-8', 'name': 'x', 'unit': '根', 'item_code': 'C2'}).status_code == 422   # 非末级
    assert client.put('/api/master/materials/M-9', headers=FIN, json={'enabled': False, 'name': '套管 φ244'}).status_code == 200
    m = next(x for x in master(client, 'materials') if x['material_code'] == 'M-9')
    assert m['enabled'] == 0 and m['name'] == '套管 φ244'
    assert [lg['action'] for lg in logs('物资:M-9')] == ['新增', '修改']
    assert '启用 是 → 否' in logs('物资:M-9')[1]['comment']


def test_unit_create_update(client):
    assert client.post('/api/master/units', headers=FIN, json={'unit': '根', 'usage': '套管'}).status_code == 200
    assert client.post('/api/master/units', headers=FIN, json={'unit': '根', 'usage': '重复'}).status_code == 409
    assert client.put('/api/master/units/根', headers=FIN, json={'usage': '套管、油管'}).status_code == 200
    assert {'unit': '根', 'usage': '套管、油管'} in master(client, 'units')
    assert [lg['action'] for lg in logs('计量单位:根')] == ['新增', '修改']
    assert client.put('/api/master/units/不存在', headers=FIN, json={'usage': 'x'}).status_code == 404


def test_new_unit_from_dropdown_registered(client):
    # 物资、报量项目下拉里新增的单位，保存时登记到计量单位表
    assert client.post('/api/master/materials', headers=FIN, json={'material_code': 'M-7', 'name': '钻杆', 'unit': '根'}).status_code == 200
    assert client.put('/api/master/report_lines/C2-1-04', headers=FIN, json={'unit': '立方米'}).status_code == 200
    units = {u['unit']: u['usage'] for u in master(client, 'units')}
    assert units['根'] == '钻杆' and '立方米' in units
    assert [lg['action'] for lg in logs('计量单位:根')] == ['新增']
    n = len(units)
    assert client.put('/api/master/materials/M-7', headers=FIN, json={'unit': '米'}).status_code == 200   # 已有单位不重复登记
    assert len(master(client, 'units')) == n


def test_report_line_update_and_disable(client):
    r = client.put('/api/master/report_lines/C2-1-04', headers=FIN, json={'label': '柴油（吨）', 'enabled': False, 'needs_doc': True, 'sort_no': 99})
    assert r.status_code == 200
    ln = next(x for x in master(client, 'report_lines') if x['line_key'] == 'C2-1-04')
    assert (ln['label'], ln['enabled'], ln['needs_doc'], ln['sort_no']) == ('柴油（吨）', 0, 1, 99)
    from app import report_lines
    from app.db import transaction
    with transaction() as c:
        assert 'C2-1-04' not in {x['line_key'] for x in report_lines.lines(c)}   # 停用后日报不再显示
    (lg,) = logs('报量项目:C2-1-04')
    assert '启用 是 → 否' in lg['comment']


def test_report_line_with_facts_cannot_be_deleted(client):
    from app.db import transaction
    with transaction() as c:
        key = c.execute("SELECT json_extract(attributes, '$.line') k FROM business_fact "
                        "WHERE json_extract(attributes, '$.line') IN (SELECT line_key FROM report_line) LIMIT 1").fetchone()['k']
    assert key
    r = client.delete(f'/api/master/report_lines/{key}', headers=FIN)
    assert r.status_code == 409 and '只能停用' in r.json()['detail']
    assert any(x['line_key'] == key for x in master(client, 'report_lines'))
    assert logs(f'报量项目:{key}') == []


def test_unused_report_line_can_be_deleted(client):
    from app import master as m
    from app.db import transaction
    with transaction() as c:
        key = next(r['line_key'] for r in c.execute('SELECT line_key FROM report_line') if not m.line_in_use(c, r['line_key']))
    assert client.delete(f'/api/master/report_lines/{key}', headers=FIN).status_code == 200
    assert all(x['line_key'] != key for x in master(client, 'report_lines'))
    assert [lg['action'] for lg in logs(f'报量项目:{key}')] == ['删除']


def test_item_only_description_fields(client):
    r = client.put('/api/master/items/C2-1-04', headers=FIN, json={'owner_dept': '物资装备部', 'finance_account': '5001'})
    assert r.status_code == 200
    it = next(x for x in master(client, 'items') if x['item_code'] == 'C2-1-04')
    assert it['owner_dept'] == '物资装备部' and it['finance_account'] == '5001'
    assert len(logs('费用科目:C2-1-04')) == 1
    for locked in ({'direction': '收入'}, {'cost_behavior': '固定'}, {'level': 1}, {'item_code': 'X'}, {'item_name': '改名'}):
        assert client.put('/api/master/items/C2-1-04', headers=FIN, json=locked).status_code == 422
    it2 = next(x for x in master(client, 'items') if x['item_code'] == 'C2-1-04')
    assert it2['direction'] == it['direction'] and it2['cost_behavior'] == it['cost_behavior']
