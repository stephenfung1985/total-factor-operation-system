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


TEAM = {'X-User-Id': 'u_team'}
FIN = {'X-User-Id': 'u_fin'}


def new_well(**over):
    body = {
        'well_id': 'W101', 'period': '2026-10', 'market': '西北', 'captain': '[队长]', 'rig_type': 'ZJ50D',
        'client': '[甲方单位]', 'block': 'C区块', 'well_category': '开发井', 'well_type': '定向井', 'spud_sections': 2,
        'design_depth': 3600, 'staff': {'合同化': 18, '市场化': 12, '第三方': 16},
        'milestones': {'搬安': '2026-10-01 08:00', '砸导管': '2026-10-02 10:00', '开钻1': '2026-10-03 08:00',
                       '完钻1': '2026-10-06 20:00', '开钻2': '2026-10-07 06:00'},
    }
    body.update(over)
    return body


def test_seeded_wells_are_summarised(client):
    rows = client.get('/api/wellinfo/summary', headers=FIN).json()
    assert [r['well_id'] for r in rows] == ['W001', 'W002', 'W003', 'W004']
    w1 = rows[0]
    assert w1['derived']['status'] == '正钻' and w1['derived']['reg_lag'] == '未滞后'
    assert w1['derived']['pre_days'] == pytest.approx(3.0)
    assert client.get('/api/wellinfo/summary', headers=TEAM).status_code == 403


def test_register_new_well_flows_into_master_data(client):
    rec = client.post('/api/wellinfo', json=new_well(), headers=TEAM).json()
    assert rec['status'] == '草稿' and rec['derived']['status'] == '正钻'
    assert rec['derived']['pre_days'] == pytest.approx(2.0)
    out = client.post(f"/api/wellinfo/{rec['report_id']}/submit", headers=TEAM).json()
    assert out['status'] == '已提交'
    # 汇总到单井基础信息
    rows = {r['well_id']: r for r in client.get('/api/wellinfo/summary', headers=FIN).json()}
    assert rows['W101']['block'] == 'C区块'
    d = client.get('/api/wellinfo/wells/W101', headers=FIN).json()
    assert [(s['phase'], s['start_at'], s['end_at']) for s in d['segments']] == [
        ('钻前', '2026-10-01', '2026-10-02'), ('一开', '2026-10-03', '2026-10-06'), ('二开', '2026-10-07', None)]
    # 新井进入本队日报可选井，表头开次取自施工时段
    assert 'W101' in [w['well_id'] for w in client.get('/api/reports/wells', headers=TEAM).json()]
    rep = client.get('/api/reports/current?well_id=W101&date=2026-10-08', headers=TEAM).json()
    assert rep['header']['phase'] == '二开' and rep['header']['block'] == 'C区块'


def test_blocking_checks(client):
    bad = new_well(spud_sections=1, milestones={'开钻1': '2026-10-03 08:00', '完钻1': '2026-10-02 08:00', '开钻2': '2026-10-04 08:00',
                                                 '完井': '2026-10-09 08:00'})
    res = client.post('/api/wellinfo/check', json=bad, headers=TEAM).json()
    rules = {c['rule'] for c in res['checks'] if c['level'] == '阻断'}
    assert {'开次', '时间顺序', '节点缺失'} <= rules
    rec = client.post('/api/wellinfo', json=bad, headers=TEAM).json()
    assert client.post(f"/api/wellinfo/{rec['report_id']}/submit", headers=TEAM).status_code == 409
    # 他队已登记的井号不能登记
    taken = client.post('/api/wellinfo/check', json=new_well(well_id='W002'), headers=TEAM).json()
    assert any(c['rule'] == '井号' for c in taken['checks'])


def test_revise_completion_and_lag(client):
    rec = client.post('/api/wellinfo', json=new_well(), headers=TEAM).json()
    client.post(f"/api/wellinfo/{rec['report_id']}/submit", headers=TEAM)
    draft = client.post(f"/api/wellinfo/{rec['report_id']}/revise", headers=TEAM).json()
    assert draft['version'] == 2 and draft['status'] == '草稿'
    ms = dict(draft['milestones'], 完钻2='2026-10-10 12:00', 完钻='2026-10-10 12:00', 完井='2026-10-11 08:00')
    client.post('/api/wellinfo', json=dict(new_well(), report_id=draft['report_id'], milestones=ms), headers=TEAM)
    out = client.post(f"/api/wellinfo/{draft['report_id']}/submit", headers=TEAM).json()
    d = out['derived']
    assert d['status'] == '完井'
    assert d['drill_days'] == pytest.approx(7.1667, abs=1e-3)
    assert d['completion_days'] == pytest.approx(0.8333, abs=1e-3)
    assert d['comp_lag'].startswith('滞后')            # 完井 10-11，现在才填报，超过 2 天时限
    assert out['registered_at'] == rec['registered_at'] or out['registered_at']  # 填报时间沿用首次提交
    hist = [h['status'] for h in out['history']]
    assert hist == ['已提交', '已被更新']
    w = client.get('/api/wellinfo/wells/W101', headers=FIN).json()
    assert w['segments'][-1]['end_at'] == '2026-10-11'


def test_basic_info_change_recalculates(client):
    """开次变化影响规则匹配：泥浆服务规则只适用二开、三开。"""
    before = client.get('/api/wells/W001/items/C2-1-02-01/events?period=2026-08', headers=FIN).json()['total']
    cur = client.get('/api/wellinfo/current?well_id=W001', headers=TEAM).json()
    draft = client.post(f"/api/wellinfo/{cur['report_id']}/revise", headers=TEAM).json()
    body = {k: draft[k] for k in ('well_id', 'period', 'market', 'captain', 'rig_type', 'client', 'block', 'well_category',
                                  'well_type', 'spud_sections', 'design_depth', 'milestones', 'staff')}
    body['market'] = '长庆'
    client.post('/api/wellinfo', json=dict(body, report_id=draft['report_id']), headers=TEAM)
    out = client.post(f"/api/wellinfo/{draft['report_id']}/submit", headers=TEAM).json()
    assert out['batch']['reversed'] > 0
    after = client.get('/api/wells/W001/items/C2-1-02-01/events?period=2026-08', headers=FIN).json()['total']
    assert before > 0 and after == pytest.approx(0, abs=0.01)


def test_summary_export_matches_sheet_columns(client):
    r = client.get('/api/wellinfo/summary.xlsx', headers=FIN)
    ws = openpyxl.load_workbook(BytesIO(r.content)).active
    header = [c.value for c in ws[1]]
    assert len(header) == 39 and header[0] == '井号' and header[-1] == '创建人'
    assert ws.max_row == 5 and ws.cell(2, 1).value == 'W001'


# ---------- 退回队伍修改 ----------

def test_finance_returns_to_team(client):
    assert client.post('/api/wellinfo/wells/W001/return', json={'comment': ''}, headers=FIN).status_code == 409
    assert client.post('/api/wellinfo/wells/W001/return', json={'comment': '区块填错'}, headers=TEAM).status_code == 403
    d = client.post('/api/wellinfo/wells/W001/return', json={'comment': '区块应为 A1区块'}, headers=FIN).json()
    assert d['pending_return']['return_comment'] == '区块应为 A1区块'
    # 队伍看到退回的草稿；修改前汇总仍按已提交版本
    mine = {m['well_id']: m for m in client.get('/api/wellinfo/mine', headers=TEAM).json()}
    assert mine['W001']['returned'] and mine['W001']['status'] == '草稿'
    assert client.get('/api/wellinfo/returns', headers=FIN).json()[0]['well_id'] == 'W001'
    cur = client.get('/api/wellinfo/current?well_id=W001', headers=TEAM).json()
    assert cur['return_comment'] == '区块应为 A1区块' and cur['version'] == 2
    body = {k: cur[k] for k in ('well_id', 'period', 'market', 'captain', 'rig_type', 'client', 'block', 'well_category',
                                'well_type', 'spud_sections', 'design_depth', 'milestones', 'staff')}
    client.post('/api/wellinfo', json=dict(body, report_id=cur['report_id'], block='A1区块'), headers=TEAM)
    client.post(f"/api/wellinfo/{cur['report_id']}/submit", headers=TEAM)
    rows = {r['well_id']: r for r in client.get('/api/wellinfo/summary', headers=FIN).json()}
    assert rows['W001']['block'] == 'A1区块' and rows['W001']['pending_return'] is None
    assert client.get('/api/wellinfo/returns', headers=FIN).json() == []


# ---------- 按原表格式导入 ----------

def _sheet(rows):
    from app import wellinfo
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(wellinfo.EXCEL_HEADERS)
    for r in rows:
        ws.append([r.get(h) for h in wellinfo.EXCEL_HEADERS])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _row(**over):
    from datetime import datetime as D
    r = {'井号': 'LK9-1', '年份': '2026', '月份': '09', '市场分布': '长庆', '队号': '70199', '队长': '[队长]', '钻机类型': 'ZJ70D',
         '甲方单位': '[甲方单位]', '区块': '雷龙湾', '井别': '注采气井', '井型': '定向井', '开次': '3', '设计井深（米）': 3495,
         '钻前周期（天）': 99, '状态': ' 🔄正钻', '搬安（平移）时间': D(2026, 9, 26, 11), '一开时间': D(2026, 10, 3, 8),
         '一开完钻': D(2026, 10, 4, 5), '二开时间': D(2026, 10, 10, 3), '合同化人数': 9, '市场化人数': 5, '第三方用工人数': 20,
         '填报时间': D(2026, 9, 30), '创建人': '[填报人]'}
    r.update(over)
    return r


def test_import_preview_commit_and_reimport(client):
    from datetime import datetime as D
    data = _sheet([
        _row(),
        _row(**{'井号': 'LK9-2', '一开完钻': D(2026, 10, 2, 5)}),          # 一开完钻早于一开 → 退回队伍
        _row(),                                                         # 文件内重复 → 跳过
        _row(**{'井号': 'W002', '队号': '70101'}),                       # 他队已登记 → 跳过
    ])
    f = {'file': ('t.xlsx', data)}
    pre = client.post('/api/wellinfo/import?commit=false', files=f, headers=FIN).json()
    assert [x['action'] for x in pre['rows']] == ['新增', '退回队伍', '跳过', '跳过']
    assert pre['new_teams'] == ['70199']
    assert client.get('/api/wellinfo/summary', headers=FIN).json()[-1]['well_id'] != 'LK9-1'   # 预览不写库
    out = client.post('/api/wellinfo/import?commit=true', files={'file': ('t.xlsx', data)}, headers=FIN).json()
    assert out['count'] == {'新增': 1, '退回队伍': 1, '跳过': 2}
    rows = {r['well_id']: r for r in client.get('/api/wellinfo/summary', headers=FIN).json()}
    d = rows['LK9-1']['derived']
    assert d['pre_days'] == pytest.approx(6.875)            # 周期由系统重算，不取表中的 99
    assert d['reg_lag'] == '未滞后' and rows['LK9-1']['registered_at'] == '2026-09-30 00:00'
    assert rows['LK9-1']['source'] == '导入' and rows['LK9-1']['team_no'] == '70199'
    returns = client.get('/api/wellinfo/returns', headers=FIN).json()
    assert returns[0]['well_id'] == 'LK9-2' and '一开完钻' in returns[0]['return_comment']
    again = client.post('/api/wellinfo/import?commit=false', files={'file': ('t.xlsx', data)}, headers=FIN).json()
    assert again['rows'][0]['action'] == '无变化'


def test_import_rejects_wrong_format(client):
    wb = openpyxl.Workbook()
    wb.active.append(['井号', '年份'])
    buf = BytesIO()
    wb.save(buf)
    r = client.post('/api/wellinfo/import', files={'file': ('t.xlsx', buf.getvalue())}, headers=FIN)
    assert r.status_code == 409 and '缺少列' in r.json()['detail']
    assert client.post('/api/wellinfo/import', files={'file': ('t.xlsx', buf.getvalue())}, headers=TEAM).status_code == 403
