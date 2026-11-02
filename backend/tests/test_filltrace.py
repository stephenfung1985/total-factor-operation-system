"""填报 AI 核对留痕：解析只记任务、不写业务表；人提交后能按字段看到建议值、最终值、是否改过。"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    for k in ('TFOS_AI_BASE_URL', 'TFOS_AI_MODEL', 'TFOS_AI_API_KEY'):
        monkeypatch.delenv(k, raising=False)
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


TEAM, FIN = {'X-User-Id': 'u_team'}, {'X-User-Id': 'u_fin'}
WHO = '70101 钻井队 · 统计员'
DAY = '2026-10-21'
HOURS = {'钻进': 16, '起下钻': 4, '循环': 2, '接单根': 1.5, '其他': 0.5}


def enable(monkeypatch, reply):
    from app import ai
    seq = list(reply) if isinstance(reply, list) else None

    def fake(_messages):
        if seq is not None:
            return seq.pop(0)
        return reply
    monkeypatch.setattr(ai, 'call_model', fake)
    monkeypatch.setenv('TFOS_AI_BASE_URL', 'http://fake/v1')
    monkeypatch.setenv('TFOS_AI_MODEL', 'fake-model')


def upload_report(client, day=DAY):
    return client.post('/api/fill/ai-extract/report', headers=TEAM,
                       data={'well_id': 'W001', 'date': day},
                       files={'file': ('日报.txt', f'{day} 起始井深4800 结束井深4850 钻进16小时 柴油3.5吨'.encode())})


def upload_well(client):
    return client.post('/api/fill/ai-extract/wellinfo', headers=TEAM,
                       files={'file': ('登记.txt', '井号 W201 西北市场 设计井深3600米 合同化18人'.encode())})


def by_key(trace, which='latest'):
    return {f['field_key']: f for f in trace[which]}


def test_extract_records_task_but_does_not_write_report(client, monkeypatch):
    enable(monkeypatch, json.dumps({
        'depth_start': 4800, 'depth_end': 4850, 'hours': {'钻进': 16, '循环': 8},
        'lines': {'C2-1-04': 3.5}, 'notes': {'C2-1-04': '单据A'}}))
    from app.db import connect
    c = connect()
    before = c.execute("SELECT COUNT(*) n FROM daily_report WHERE well_id='W001' AND report_date=?", (DAY,)).fetchone()['n']
    r = upload_report(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['task_id'] and body['lines']['C2-1-04'] == 3.5 and body['labels']['C2-1-04'] == '柴油'
    after = connect().execute("SELECT COUNT(*) n FROM daily_report WHERE well_id='W001' AND report_date=?", (DAY,)).fetchone()['n']
    assert before == after == 0                                          # 建议值不落成日报
    task = connect().execute('SELECT * FROM ai_task WHERE task_id=?', (body['task_id'],)).fetchone()
    assert task['task_type'] == '日报解析' and task['status'] == '完成' and task['model'] == 'fake-model'
    assert task['created_by'] == WHO and 'fake-model' == task['model']
    stored = json.loads(task['output'])
    assert stored['suggestions']['depth_end'] == 4850 and stored['team_id'] == 't1'
    # 规则解析任务列表不混入填报解析
    assert all(t['task_id'] != body['task_id'] for t in client.get('/api/ai/tasks', headers=FIN).json())
    c.close()


def test_failed_extract_does_not_leave_a_task(client, monkeypatch):
    r = upload_report(client)
    assert r.status_code == 503                                          # 未配置，不调用、不记任务
    enable(monkeypatch, '{}')
    r = upload_report(client)
    assert r.status_code == 422
    from app.db import connect
    c = connect()
    assert c.execute("SELECT COUNT(*) n FROM ai_task WHERE task_type='日报解析'").fetchone()['n'] == 0
    c.close()


def test_submit_traces_each_ai_field(client, monkeypatch):
    enable(monkeypatch, [
        json.dumps({'depth_start': 4800, 'depth_end': 4850, 'hours': {'钻进': 16, '循环': 8},
                    'lines': {'C2-1-04': 3.5}, 'notes': {'C2-1-04': '单据A'}}),
        json.dumps({'depth_start': 4800, 'depth_end': 4860, 'hours': {'钻进': 10}}),  # 后一次覆盖井深和钻进，柴油仍归第一次
    ])
    first = upload_report(client).json()
    second = upload_report(client).json()
    assert first['task_id'] != second['task_id']
    draft = {
        'well_id': 'W001', 'report_date': DAY, 'depth_start': 4800, 'depth_end': 4840,
        'hours': HOURS, 'lines': {'C2-1-04': 3.5}, 'notes': {'C2-1-04': '单据B'},
        'ai_task_ids': [first['task_id'], second['task_id']],
    }
    saved = client.post('/api/reports', json=draft, headers=TEAM)
    assert saved.status_code == 200, saved.text
    assert saved.json()['status'] == '草稿' and saved.json()['ai_task_ids'] == [first['task_id'], second['task_id']]
    rid = saved.json()['report_id']
    empty = client.get(f'/api/fill/ai-trace?object_type=日报&object_id={rid}', headers=TEAM).json()
    assert empty['summary']['fields'] == 0                               # 草稿阶段还没有最终值
    out = client.post(f'/api/reports/{rid}/submit', headers=TEAM)
    assert out.status_code == 200, out.text
    assert out.json()['status'] == '已审核'                              # 仍是人提交后才生效
    assert any(h['action'] == 'AI核对留痕' for h in out.json()['history'])
    trace = client.get(f'/api/fill/ai-trace?object_type=日报&object_id={rid}', headers=TEAM).json()
    fields = by_key(trace)
    assert set(fields) == {'depth_start', 'depth_end', 'hours.钻进', 'hours.循环', 'lines.C2-1-04', 'doc.C2-1-04'}
    assert 'hours.起下钻' not in fields                                  # 人另填的、AI 没建议的，不算 AI 字段
    end, drill, fuel, doc, start, circ = (fields[k] for k in
                                          ('depth_end', 'hours.钻进', 'lines.C2-1-04', 'doc.C2-1-04', 'depth_start', 'hours.循环'))
    assert end['suggested'] == '4860' and end['submitted'] == '4840' and end['modified'] is True
    assert end['ai_task_id'] == second['task_id']                        # 同一字段以后一次解析为准
    assert drill['suggested'] == '10' and drill['submitted'] == '16' and drill['modified'] is True
    assert drill['ai_task_id'] == second['task_id']
    assert fuel['suggested'] == '3.5' and fuel['submitted'] == '3.5' and fuel['modified'] is False
    assert fuel['field_label'] == '柴油' and fuel['ai_task_id'] == first['task_id']
    assert doc['suggested'] == '单据A' and doc['submitted'] == '单据B' and doc['modified'] is True
    assert start['modified'] is False and circ['suggested'] == '8' and circ['submitted'] == '2' and circ['modified'] is True
    for f in fields.values():
        assert f['operator'] == WHO and f['submitted_at'] and f['ai_task_id'] in (first['task_id'], second['task_id'])
    assert trace['summary'] == {'fields': 6, 'modified': 4, 'unchanged': 2, 'captures': 1}
    assert trace['tasks'][0]['task_type'] == '日报解析' and trace['tasks'][0]['model'] == 'fake-model'
    # 管理端能看清单和明细；别的队看不到这张日报
    groups = client.get('/api/fill/ai-traces', headers=FIN).json()
    assert groups[0]['object_type'] == '日报' and groups[0]['well_id'] == 'W001' and groups[0]['modified'] == 4
    assert client.get('/api/fill/ai-traces', headers=TEAM).status_code == 403
    fin = client.get(f'/api/fill/ai-trace?object_type=日报&object_id={rid}', headers=FIN).json()
    assert fin['summary']['fields'] == 6
    from app.db import transaction
    with transaction() as c:
        c.execute("INSERT INTO app_user (user_id, name, role, who, team_id, scope_label) VALUES (?,?,?,?,?,?)",
                  ('u_t2', '统计员', 'team', '70102 钻井队 · 统计员', 't2', '70102 钻井队'))
    assert client.get(f'/api/fill/ai-trace?object_type=日报&object_id={rid}', headers={'X-User-Id': 'u_t2'}).status_code == 404
    # 更正版本沿用解析任务：再改结束井深并提交，新版本另记一笔，原版本留痕不动
    corrected = client.post(f'/api/reports/{rid}/correct', headers=TEAM).json()
    assert corrected['ai_task_ids'] == [first['task_id'], second['task_id']]
    revised = dict(draft, depth_end=4830, ai_task_ids=None)
    revised.pop('ai_task_ids')
    put = client.put(f"/api/reports/{corrected['report_id']}", json=revised, headers=TEAM)
    assert put.status_code == 200, put.text
    assert put.json()['ai_task_ids'] == [first['task_id'], second['task_id']]   # 不传任务号则保留
    client.post(f"/api/reports/{corrected['report_id']}/submit", headers=TEAM)
    new_trace = client.get(f"/api/fill/ai-trace?object_type=日报&object_id={corrected['report_id']}", headers=FIN).json()
    assert by_key(new_trace)['depth_end']['submitted'] == '4830' and by_key(new_trace)['depth_end']['modified'] is True
    old = client.get(f'/api/fill/ai-trace?object_type=日报&object_id={rid}', headers=FIN).json()
    assert by_key(old)['depth_end']['submitted'] == '4840' and old['summary']['captures'] == 1


def test_rejects_foreign_or_mismatched_task(client, monkeypatch):
    enable(monkeypatch, json.dumps({'depth_end': 4850, 'hours': {'钻进': 16, '循环': 8}}))
    task_id = upload_report(client).json()['task_id']
    bad_day = dict(well_id='W001', report_date='2026-10-22', depth_start=4800, depth_end=4850, hours=HOURS, ai_task_ids=[task_id])
    r = client.post('/api/reports', json=bad_day, headers=TEAM)
    assert r.status_code == 409 and '不能用于本张日报' in r.json()['detail']
    from app.db import connect
    assert connect().execute("SELECT COUNT(*) n FROM daily_report WHERE report_date='2026-10-22'").fetchone()['n'] == 0
    missing = dict(bad_day, report_date=DAY, ai_task_ids=[99999])
    assert client.post('/api/reports', json=missing, headers=TEAM).status_code == 409
    from app.db import jdump, now, transaction
    with transaction() as c:
        c.execute(
            'INSERT INTO ai_task (task_type, status, prompt_version, output, draft_count, created_by, created_at, finished_at) '
            "VALUES ('日报解析', '完成', 'fill-parse-v1', ?, 1, ?, ?, ?)",
            (jdump({'kind': '日报', 'team_id': 't2', 'well_id': 'W001', 'report_date': DAY, 'suggestions': {'depth_end': 1}}),
             '70102 钻井队 · 统计员', now(), now()))
        other = c.execute('SELECT MAX(task_id) n FROM ai_task').fetchone()['n']
    stolen = dict(bad_day, report_date=DAY, ai_task_ids=[other])
    r = client.post('/api/reports', json=stolen, headers=TEAM)
    assert r.status_code == 409 and '不是本队' in r.json()['detail']
    assert client.get('/api/fill/ai-trace?object_type=不存在&object_id=1', headers=FIN).status_code == 422


def test_wellinfo_trace(client, monkeypatch):
    enable(monkeypatch, json.dumps({
        'fields': {'well_id': 'W201', 'period': '2026-10', 'market': '西北', 'captain': '张三', 'rig_type': 'ZJ50D',
                   'client': '某甲方', 'block': 'C区块', 'well_category': '开发井', 'well_type': '定向井',
                   'spud_sections': 2, 'design_depth': '3600米'},
        'milestones': {'搬安': '2026-10-01 08:00', '开钻1': '2026-10-03 08:00'},
        'staff': {'合同化': 18, '市场化': 12}}))
    got = upload_well(client)
    assert got.status_code == 200, got.text
    task_id = got.json()['task_id']
    from app.db import connect
    assert connect().execute("SELECT COUNT(*) n FROM well_info_report WHERE well_id='W201'").fetchone()['n'] == 0
    assert connect().execute("SELECT COUNT(*) n FROM well WHERE well_id='W201'").fetchone()['n'] == 0
    body = {
        'well_id': 'W201', 'period': '2026-10', 'market': '长庆', 'captain': '张三', 'rig_type': 'ZJ50D',
        'client': '某甲方', 'block': 'C区块', 'well_category': '开发井', 'well_type': '定向井', 'spud_sections': 2,
        'design_depth': 3600, 'staff': {'合同化': 18, '市场化': 12, '第三方': 4},
        'milestones': {'搬安': '2026-10-01 08:00', '开钻1': '2026-10-03 09:30'},
        'ai_task_ids': [task_id],
    }
    draft = client.post('/api/wellinfo', json=body, headers=TEAM).json()
    assert draft['status'] == '草稿'
    assert connect().execute("SELECT COUNT(*) n FROM well WHERE well_id='W201'").fetchone()['n'] == 0   # 保存草稿也不进主数据
    out = client.post(f"/api/wellinfo/{draft['report_id']}/submit", headers=TEAM)
    assert out.status_code == 200, out.text
    assert out.json()['status'] == '已提交'
    trace = client.get(f"/api/fill/ai-trace?object_type=新开井&object_id={draft['report_id']}", headers=TEAM).json()
    fields = by_key(trace)
    assert fields['fields.market']['suggested'] == '西北' and fields['fields.market']['submitted'] == '长庆'
    assert fields['fields.market']['modified'] is True and fields['fields.market']['ai_task_id'] == task_id
    assert fields['fields.design_depth']['suggested'] == '3600' and fields['fields.design_depth']['modified'] is False
    assert fields['milestones.开钻1']['suggested'] == '2026-10-03 08:00' and fields['milestones.开钻1']['submitted'] == '2026-10-03 09:30'
    assert fields['milestones.开钻1']['modified'] is True and fields['milestones.搬安']['modified'] is False
    assert fields['staff.合同化']['modified'] is False and 'staff.第三方' not in fields
    assert fields['fields.market']['operator'] == WHO and fields['fields.market']['submitted_at']
    listed = client.get('/api/fill/ai-traces', headers=FIN).json()
    assert any(g['object_type'] == '新开井' and g['well_id'] == 'W201' for g in listed)
    # 更新版本沿用任务号，再改市场后提交，新版本留痕单独成笔
    rev = client.post(f"/api/wellinfo/{draft['report_id']}/revise", headers=TEAM).json()
    assert task_id in rev['ai_task_ids']
    body.update(report_id=rev['report_id'], market='大港')
    body.pop('ai_task_ids')
    client.post('/api/wellinfo', json=body, headers=TEAM)
    client.post(f"/api/wellinfo/{rev['report_id']}/submit", headers=TEAM)
    again = client.get(f"/api/fill/ai-trace?object_type=新开井&object_id={rev['report_id']}", headers=FIN).json()
    assert by_key(again)['fields.market']['submitted'] == '大港' and by_key(again)['fields.market']['modified'] is True
    first = client.get(f"/api/fill/ai-trace?object_type=新开井&object_id={draft['report_id']}", headers=FIN).json()
    assert by_key(first)['fields.market']['submitted'] == '长庆'
