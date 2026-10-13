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


FIN = {'X-User-Id': 'u_fin'}


def _sheet_from_export(client, **head):
    """用 W001 8 月的导出文件改表头，模拟一份历史模板。"""
    r = client.get('/api/wells/W001/export.xlsx?period=2026-08', headers=FIN)
    wb = openpyxl.load_workbook(BytesIO(r.content))
    ws = wb.active
    rows = {ws.cell(i, 1).value: i for i in range(1, 19)}
    for k, v in head.items():
        ws.cell(rows[k], 2).value = v
    return wb


def _bytes(wb):
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post(client, data, commit):
    return client.post(f'/api/results/import?commit={str(commit).lower()}', files={'file': ('历史.xlsx', data)}, headers=FIN)


def test_role_renamed(client):
    assert client.get('/api/me', headers={'X-User-Id': 'u_team'}).json()['role_name'] == '基层钻井队'


def test_blank_template(client):
    r = client.get('/api/results/template.xlsx', headers=FIN)
    ws = openpyxl.load_workbook(BytesIO(r.content)).active
    assert ws.max_row == 174 and ws.cell(1, 1).value == '井号'
    assert all(ws.cell(i, 2).value is None for i in range(1, 175))
    assert client.get('/api/results/template.xlsx', headers={'X-User-Id': 'u_team'}).status_code == 403


def test_import_new_well_and_remark(client):
    expected = client.get('/api/wells/W001/result?period=2026-08', headers=FIN).json()['kpi']
    data = _bytes(_sheet_from_export(client, 井号='H001', 月份='07', 队号='BH50305', 区块='塔中'))
    pre = _post(client, data, False).json()
    s = pre['sheets'][0]
    assert s['action'] == '导入' and s['new_well'] and s['new_team'] and s['period'] == '2026-07'
    assert s['total_cost'] == pytest.approx(expected['total_cost'], abs=0.05)
    assert client.get('/api/overview?period=2026-07', headers=FIN).json()['wells'] == []      # 预览不写库

    assert _post(client, data, True).json()['count'] == {'导入': 1}
    ov = client.get('/api/overview?period=2026-07', headers=FIN).json()['wells']
    assert [(w['well_id'], w['remark'], w['team']) for w in ov] == [('H001', '历史数据导入', 'BH50305 钻井队')]
    k = ov[0]
    assert k['total_cost'] == pytest.approx(expected['total_cost'], abs=0.05)
    assert k['net_profit'] == pytest.approx(expected['net_profit'], abs=0.05)
    # 已导入的计提科目不再由比例规则重复计算
    ev = client.get('/api/wells/H001/items/C2-3-30/events?period=2026-07', headers=FIN).json()['events']
    assert [e['origin'] for e in ev] == ['历史导入']
    info = client.get('/api/wells/H001/result?period=2026-07', headers=FIN).json()['info']
    assert info['remark'] == '历史数据导入' and '历史.xlsx' in info['import_sources'][0]


def test_reimport_unchanged_then_overwrite(client):
    wb = _sheet_from_export(client, 井号='H002', 月份='07', 队号='70101')
    data = _bytes(wb)
    _post(client, data, True)
    assert _post(client, data, False).json()['sheets'][0]['action'] == '无变化'
    ws = wb.active
    for i in range(19, ws.max_row + 1):
        if ws.cell(i, 1).value == '4.柴油':
            ws.cell(i, 2).value = 200000
    data2 = _bytes(wb)
    pre = _post(client, data2, False).json()['sheets'][0]
    assert pre['action'] == '覆盖' and any('合计' in w for w in pre['warns'])   # 改了明细未改汇总行
    before = client.get('/api/overview?period=2026-07', headers=FIN).json()['wells'][0]['total_cost']
    _post(client, data2, True)
    after = client.get('/api/overview?period=2026-07', headers=FIN).json()['wells'][0]['total_cost']
    assert after - before == pytest.approx(200000 - 129386.68, abs=0.5)
    ev = client.get('/api/wells/H002/items/C2-1-04/events?period=2026-07', headers=FIN).json()['events']
    assert sorted(e['status'] for e in ev) == ['冲销', '已冲销', '有效']


def test_import_rounds_amounts_to_whole_yuan(client):
    wb = _sheet_from_export(client, 井号='H003', 月份='07', 队号='70101')
    ws = wb.active
    row = next(i for i in range(19, ws.max_row + 1) if ws.cell(i, 1).value == '4.柴油')
    ws.cell(row, 2).value = 100.5
    data = _bytes(wb)
    pre = _post(client, data, False).json()['sheets'][0]
    assert float(pre['total_cost']).is_integer() and float(pre['revenue']).is_integer()
    _post(client, data, True)
    ev = client.get('/api/wells/H003/items/C2-1-04/events?period=2026-07', headers=FIN).json()['events']
    assert [e['amount'] for e in ev] == [101]
    ov = client.get('/api/overview?period=2026-07', headers=FIN).json()['wells'][0]
    assert ov['total_cost'] == pre['total_cost']      # 预览合计与入库后一致


def test_import_blocked_for_system_period(client):
    data = _bytes(_sheet_from_export(client, 月份='10'))
    s = _post(client, data, False).json()['sheets'][0]
    assert s['action'] == '阻断' and any('规则计算' in b for b in s['blocks'])


def test_import_rejects_wrong_layout(client):
    wb = _sheet_from_export(client, 井号='H003', 月份='07', 队号='70101')
    wb.active.cell(30, 1).value = '随便写的项目'
    s = _post(client, _bytes(wb), False).json()['sheets'][0]
    assert s['action'] == '阻断' and '与模板' in s['blocks'][0]
    bad = openpyxl.Workbook()
    bad.active.append(['项目', '金额'])
    assert _post(client, _bytes(bad), False).status_code == 409


def test_export_team_no(client):
    rows = client.get('/api/wells/W001/export/preview?period=2026-08', headers=FIN).json()
    assert next(r['v'] for r in rows if r['k'] == '队号') == '70101'


def test_benchmark_dimensions(client):
    one = client.get('/api/benchmark?period=2026-08&dims=market', headers=FIN).json()
    assert len(one['groups']) == 1 and one['groups'][0]['well_count'] == 4
    assert one['total']['total_cost'] == pytest.approx(one['groups'][0]['total_cost'])
    blk = client.get('/api/benchmark?period=2026-08&dims=block', headers=FIN).json()
    assert {g['keys']['block'] for g in blk['groups']} == {'A区块', 'B区块'}
    assert sum(g['total_cost'] for g in blk['groups']) == pytest.approx(blk['total']['total_cost'])
    multi = client.get('/api/benchmark?period=2026-08&dims=market,block,team', headers=FIN).json()
    assert len(multi['groups']) == 4 and set(multi['groups'][0]['keys']) == {'market', 'block', 'team'}
    g = next(x for x in multi['groups'] if x['keys']['team'] == '70101 钻井队')
    assert g['footage'] == pytest.approx(4411) and g['cost_per_m'] == pytest.approx(g['total_cost'] / 4411)
    assert g['net_rate'] == pytest.approx(g['net_profit'] / g['revenue'])
    allp = client.get('/api/benchmark?period=all&dims=team', headers=FIN).json()
    assert allp['total']['well_months'] == 5     # 8 月 4 口 + 10 月 W001
    assert client.get('/api/benchmark?period=all&dims=team', headers={'X-User-Id': 'u_dept'}).status_code == 403


def test_benchmark_specific_objects(client):
    # 只对比两支队伍：70101 与 70103
    r = client.get('/api/benchmark?period=2026-08&dims=team&team=70101 钻井队&team=70103 钻井队', headers=FIN).json()
    assert r['filtered'] and sorted(g['keys']['team'] for g in r['groups']) == ['70101 钻井队', '70103 钻井队']
    assert r['scope']['well_count'] == 2 and r['total']['well_count'] == 4
    assert r['scope']['total_cost'] == pytest.approx(sum(g['total_cost'] for g in r['groups']))
    # 选项联动：选了 B区块，队伍只剩 B区块的两支；区块自身的选项不受影响
    r = client.get('/api/benchmark?period=2026-08&dims=team&block=B区块', headers=FIN).json()
    assert [o['value'] for o in r['options']['team']] == ['70103 钻井队', '70104 钻井队']
    assert [o['value'] for o in r['options']['block']] == ['A区块', 'B区块']
    assert {g['keys']['team'] for g in r['groups']} == {'70103 钻井队', '70104 钻井队'}
    # 区块对区块
    r = client.get('/api/benchmark?period=2026-08&dims=block&block=A区块&block=B区块', headers=FIN).json()
    a, b = sorted(r['groups'], key=lambda g: g['keys']['block'])
    assert a['well_count'] == 2 and b['well_count'] == 2
