from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


DEPT = {'X-User-Id': 'u_dept'}
FIN = {'X-User-Id': 'u_fin'}
TEAM = {'X-User-Id': 'u_team'}
DIMS = ['period', 'market', 'block', 'team']


def get(client, h, by='period', **q):
    return client.get('/api/cost-detail', params={'by': by, **q}, headers=h)


def test_dept_scope_and_no_revenue(client):
    opts = client.get('/api/cost-detail/options', headers=DEPT).json()
    codes = {i['code'] for i in opts['items']}
    assert codes and not any(c.startswith('R') for c in codes)          # 部室看不到收入
    fin = client.get('/api/cost-detail/options', headers=FIN).json()
    assert len(fin['items']) > len(opts['items'])                       # 经营总览看全部成本科目
    leaf_depts = {i['dept'] for i in opts['items'] if i['is_leaf']}
    assert all('技术' in d.split('/') for d in leaf_depts)               # 部室只含归口科目


@pytest.mark.parametrize('h,status', [(TEAM, 403)])
def test_other_roles_forbidden(client, h, status):
    assert get(client, h).status_code == status
    assert client.get('/api/cost-detail/export.xlsx', headers=h).status_code == status


@pytest.mark.parametrize('by', DIMS)
def test_every_dimension_reconciles_with_overview(client, by):
    from app import analysis, costdetail
    from app.db import transaction
    m = get(client, DEPT, by).json()
    assert m['label'] == costdetail.DIMS[by] and m['columns']
    assert sum(c['total'] for c in m['columns']) == m['grand_total']
    leaves = [r for r in m['rows'] if r['is_leaf']]
    assert sum(r['total'] for r in leaves) == m['grand_total']
    for c in m['columns']:      # 列合计等于该列各叶子科目之和
        assert sum(r['values'][c['key']] for r in leaves) == c['total']
    with transaction() as conn:   # 与经营总览同源：各井各期部室可见成本合计
        user = dict(conn.execute("SELECT * FROM app_user WHERE user_id='u_dept'").fetchone())
        want = 0
        for r in conn.execute('SELECT DISTINCT well_id, period FROM economic_event').fetchall():
            k = analysis.compute(conn, user, r['well_id'], r['period'])
            want += sum(n['amount'] for n in k['tree'] if n['is_leaf'])
    assert m['grand_total'] == want


def test_parent_rollup_and_selection(client):
    m = get(client, DEPT, 'market').json()
    by_code = {r['code']: r for r in m['rows']}
    parents = [r for r in m['rows'] if not r['is_leaf']]
    assert parents
    p = parents[0]
    kids = [r for r in m['rows'] if r['parent'] == p['code']]
    assert p['total'] == sum(k['total'] for k in kids)
    sel = get(client, DEPT, 'market', items=p['code']).json()
    got = {r['code'] for r in sel['rows']}
    assert p['code'] in got and all(k['code'] in got for k in kids)
    assert got <= set(by_code)                       # 只含所选科目及其下级
    assert sel['grand_total'] == sum(r['total'] for r in sel['rows'] if r['is_leaf'])


def test_unknown_or_hidden_item_rejected(client):
    assert get(client, DEPT, items='NOPE').status_code == 400
    assert get(client, DEPT, items='R02').status_code == 400          # 收入科目对部室不可见


def test_period_range(client):
    full = get(client, DEPT, 'period').json()
    periods = [c['key'] for c in full['columns']]
    assert len(periods) >= 2
    one = get(client, DEPT, 'period', start=periods[0], end=periods[0]).json()
    assert [c['key'] for c in one['columns']] == [periods[0]]
    assert one['grand_total'] == full['columns'][0]['total']
    assert get(client, DEPT, start='2026-10', end='2026-08').status_code == 400
    assert get(client, DEPT, start='2026-13').status_code == 400


def test_export_workbook_and_audit_log(client):
    r = client.get('/api/cost-detail/export.xlsx', headers=DEPT)
    assert r.status_code == 200 and 'spreadsheetml' in r.headers['content-type']
    wb = openpyxl.load_workbook(BytesIO(r.content))
    assert wb.sheetnames == ['按时间', '按市场', '按区块', '按队伍', '事项明细']
    m = get(client, DEPT, 'team').json()
    ws = wb['按队伍']
    header = [c.value for c in ws[3]]
    assert header[:4] == ['科目编码', '科目名称', '归口部室', '成本性质'] and header[-1] == '合计'
    assert [c['label'] for c in m['columns']] == header[4:-1]
    detail = wb['事项明细']
    assert detail.max_row - 1 == m['event_count']                       # 明细逐笔，与汇总同源
    assert sum(row[13] for row in detail.iter_rows(min_row=2, values_only=True)) == m['grand_total']
    text = ' '.join(str(c.value) for s in wb for row in s.iter_rows() for c in row if c.value is not None)
    assert '形象收入' not in text and '合同收入' not in text            # 部室导出不含收入

    one = client.get('/api/cost-detail/export.xlsx', params={'by': 'block', 'detail': 'false'}, headers=DEPT)
    assert openpyxl.load_workbook(BytesIO(one.content)).sheetnames == ['按区块']
    assert client.get('/api/cost-detail/export.xlsx', params={'by': 'bogus'}, headers=DEPT).status_code == 400

    from app.db import transaction
    with transaction() as c:
        n = c.execute("SELECT COUNT(*) FROM approval_log WHERE object_type='导出' AND object_id='cost-detail'").fetchone()[0]
    assert n == 2
