import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


def H(user):
    return {'X-User-Id': user}


def _submitted(client, lines=None, date='2026-10-10'):
    body = {'well_id': 'W001', 'report_date': date, 'depth_start': 4824, 'depth_end': 4874,
            'hours': {'钻进': 16, '起下钻': 4, '循环': 2, '接单根': 1.5, '其他': 0.5}, 'lines': lines or {}}
    rid = client.post('/api/reports', json=body, headers=H('u_team')).json()['report_id']
    assert client.post(f'/api/reports/{rid}/submit', headers=H('u_team')).status_code == 200
    return rid


def _c105(client, period='2026-10'):
    r = client.get(f'/api/wells/W001/items/C1-05/events?period={period}', headers=H('u_fin')).json()
    return sum(e['amount'] for e in r['events'])


# ---------- 费用分摊 ----------

def test_allocation_replaces_rule_amount_and_void_restores(client):
    _submitted(client)   # 提交即生效
    rule_amt = _c105(client)
    assert rule_amt > 0
    body = {'period': '2026-10', 'item_code': 'C1-05', 'basis': '队天', 'amount': 100000}
    assert client.post('/api/allocation/pools', json=body, headers=H('u_team')).status_code == 403
    pv = client.post('/api/allocation/preview', json=body, headers=H('u_fin')).json()
    assert pv['total'] == 100000 and sum(s['amount'] for s in pv['shares']) == 100000
    out = client.post('/api/allocation/pools', json=body, headers=H('u_fin'))
    assert out.status_code == 200
    pid = out.json()['pool_id']
    assert _c105(client) == 100000                      # 规则算的制造费被分摊金额取代，不重复计入
    assert client.post('/api/allocation/pools', json=body, headers=H('u_fin')).status_code == 409   # 同范围不能重复建池
    ev = next(e for e in client.get('/api/wells/W001/items/C1-05/events?period=2026-10', headers=H('u_fin')).json()['events']
              if e['origin'] == '分摊' and e['status'] == '有效')
    t = client.get(f"/api/events/{ev['event_id']}/trace", headers=H('u_fin')).json()
    assert t['src_title'] == '费用池' and '费用池' in t['formula']
    d = client.get(f'/api/allocation/pools/{pid}', headers=H('u_fin')).json()
    assert d['total'] == 100000 and d['events'][0]['well_id'] == 'W001'
    assert client.post(f'/api/allocation/pools/{pid}/void', headers=H('u_fin')).json()['reversed'] == 1
    assert _c105(client) == pytest.approx(rule_amt, abs=1)   # 作废后恢复规则计算
    assert client.post(f'/api/allocation/pools/{pid}/void', headers=H('u_fin')).status_code == 409


def test_allocation_validations(client):
    base = {'period': '2026-10', 'basis': '队天', 'amount': 1000}
    assert client.post('/api/allocation/preview', json={**base, 'item_code': 'C2-1-04'}, headers=H('u_fin')).status_code == 422
    assert client.post('/api/allocation/preview', json={**base, 'item_code': 'C1-05', 'amount': 0}, headers=H('u_fin')).status_code == 422
    assert client.post('/api/allocation/preview', json={**base, 'item_code': 'C1-05', 'period': '2030-01'},
                       headers=H('u_fin')).status_code == 409   # 无已审核事实
    # 已有历史导入金额的井不能再分摊
    assert client.post('/api/allocation/preview', json={**base, 'item_code': 'C1-05', 'period': '2026-08'},
                       headers=H('u_fin')).status_code in (409,)


def test_allocation_splits_by_basis_with_tail_to_last_well():
    from app.allocation import shares
    sh = shares(100, [{'w': 1, 'well_id': 'A'}, {'w': 1, 'well_id': 'B'}, {'w': 1, 'well_id': 'C'}])
    assert [s['amount'] for s in sh] == [33, 33, 34]


# ---------- 基础信息库按角色裁剪 ----------

def test_info_library_is_role_scoped(client):
    fin = client.get('/api/wellinfo/library', headers=H('u_fin')).json()
    team = client.get('/api/wellinfo/library', headers=H('u_team')).json()
    keys = lambda d: {c['key'] for c in d['columns']}
    assert fin['rows'] and {'captain', 'staff_total', 'reg_lag'} <= keys(fin) and keys(team) == keys(fin)
    assert all('captain' in r and 'staff' in r for r in fin['rows'])
    assert {r['well_id'] for r in team['rows']} < {r['well_id'] for r in fin['rows']}
    assert all(r['team_no'] == team['rows'][0]['team_no'] for r in team['rows'])


# ---------- 元/台月：年度池 ----------

def test_rig_month_basis_annual_pool(client):
    body = {'period': '2026', 'item_code': 'C1-03-03', 'basis': '台月', 'amount': 1200000}
    pv = client.post('/api/allocation/preview', json=body, headers=H('u_fin')).json()
    assert pv['unit'] == '台月' and pv['period'] == '2026'
    assert pv['rate'] == pytest.approx(1200000 / pv['basis_total'], rel=1e-5)    # 标准 = 费用池 ÷ 全年实际钻机台月
    # 8 月各井已有历史导入，跳过不分；其余井月按 标准 × 当月台月
    assert {s['period'] for s in pv['skipped']} == {'2026-08'}
    assert '2026-08' not in {s['period'] for s in pv['shares']}
    for s in pv['shares']:
        assert s['amount'] == pytest.approx(pv['rate'] * s['w'], abs=1)
    out = client.post('/api/allocation/pools', json=body, headers=H('u_fin')).json()
    d = client.get(f"/api/allocation/pools/{out['pool_id']}", headers=H('u_fin')).json()
    assert d['total'] == pv['allocated'] and d['rate'] == pytest.approx(pv['rate'], abs=0.01)
    assert {e['period'] for e in d['events']} == {s['period'] for s in pv['shares']}
    ev = client.get('/api/wells/W001/items/C1-03-03/events?period=2026-09', headers=H('u_fin')).json()['events']
    a = next(e for e in ev if e['origin'] == '分摊')
    t = client.get(f"/api/events/{a['event_id']}/trace", headers=H('u_fin')).json()
    assert '元/台月' in t['formula']
    assert client.post(f"/api/allocation/pools/{out['pool_id']}/void", headers=H('u_fin')).json()['reversed'] == len(d['events'])


def test_rig_months_split_by_calendar_month(client):
    from datetime import date
    from app.allocation import rig_months
    from app.db import connect
    c = connect()
    c.execute("INSERT INTO team_well_period (team_id, well_id, phase, start_at, end_at) "
              "SELECT team_id, 'W001', '测试', '2025-01-17', '2025-03-15' FROM team LIMIT 1")
    rows = [r for r in rig_months(c, '2025', None, today=date(2025, 12, 31)) if r['well_id'] == 'W001']
    c.close()
    got = {r['period']: round(r['w'], 4) for r in rows}
    assert got == {'2025-01': round(15 / 31, 4), '2025-02': 1.0, '2025-03': round(15 / 31, 4)}


def test_annual_and_monthly_pools_cannot_overlap(client):
    from app.db import connect
    c = connect()
    c.execute("INSERT INTO cost_pool (period, item_code, basis, amount, status, created_by, created_at) "
              "VALUES ('2026', 'C1-05', '台月', 1, '有效', 't', 't')")
    c.commit()
    c.close()
    _submitted(client)   # 提交即生效
    r = client.post('/api/allocation/preview', json={'period': '2026-10', 'item_code': 'C1-05', 'basis': '队天', 'amount': 1000},
                    headers=H('u_fin'))
    assert r.status_code == 409 and '重叠' in r.json()['detail']
