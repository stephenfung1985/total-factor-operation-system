"""取消专业部室审核后的容错与补救：日报提交即生效 + 管理端抽查 / 退回；规则影响预览 + 变化依据 + 回滚。"""
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


FIN, TEAM = H('u_fin'), H('u_team')


def body(depth_end=4874, date='2026-10-10'):
    return {'well_id': 'W001', 'report_date': date, 'depth_start': 4824, 'depth_end': depth_end,
            'hours': {'钻进': 16, '起下钻': 4, '循环': 2, '接单根': 1.5, '其他': 0.5}, 'lines': {'C2-1-04': 4.4}}


def submit(client, depth_end=4874):
    rid = client.post('/api/reports', json=body(depth_end), headers=TEAM).json()['report_id']
    out = client.post(f'/api/reports/{rid}/submit', headers=TEAM).json()
    return rid, out


def revenue(client):
    return client.get('/api/wells/W001/result?period=2026-10', headers=FIN).json()['kpi']['revenue']


def ids(client, tab):
    return [r['report_id'] for r in client.get(f'/api/monitor/reports?tab={tab}', headers=FIN).json()]


IMAGE = round(7147828.84 / 4411, 4)


# ---------- 日报：提交即生效 ----------

def test_warning_report_takes_effect_and_waits_for_spot_check(client):
    rid, out = submit(client, 4824 + 320)          # 单日进尺 320 米：警告，不阻断
    assert out['status'] == '已审核' and out['reviewer'] == '系统校验'
    assert rid in ids(client, 'todo') and rid in ids(client, 'effective')
    assert client.get('/api/monitor/reports?tab=todo', headers=TEAM).status_code == 403
    client.post(f'/api/reports/{rid}/check', json={'comment': '已电话核实'}, headers=FIN)
    assert rid not in ids(client, 'todo')
    assert client.get('/api/monitor/summary', headers=FIN).json()['todo'] == len(ids(client, 'todo'))


def test_return_for_correction_keeps_original_until_team_resubmits(client):
    before = revenue(client)
    rid, _ = submit(client)
    assert revenue(client) - before == pytest.approx(50 * IMAGE, abs=1)
    assert client.post(f'/api/reports/{rid}/return', json={'comment': ''}, headers=FIN).status_code == 409   # 必须写原因
    draft = client.post(f'/api/reports/{rid}/return', json={'comment': '进尺与钻井日报不符'}, headers=FIN).json()
    assert draft['status'] == '驳回' and draft['supersedes_report_id'] == rid and '管理端退回' in draft['review_comment']
    assert client.get(f'/api/reports/{rid}', headers=FIN).json()['status'] == '已审核'       # 原版本保持生效
    assert revenue(client) - before == pytest.approx(50 * IMAGE, abs=1)
    assert draft['report_id'] in ids(client, 'returned')
    mine = {m['report_id']: m for m in client.get('/api/reports/mine', headers=TEAM).json()}
    assert mine[draft['report_id']]['status'] == '驳回'                                         # 队伍待办
    client.put(f"/api/reports/{draft['report_id']}", json=body(4864), headers=TEAM)
    out = client.post(f"/api/reports/{draft['report_id']}/submit", headers=TEAM).json()
    assert out['status'] == '已审核' and out['batch']['reversed'] > 0
    assert client.get(f'/api/reports/{rid}', headers=FIN).json()['status'] == '已被更正'
    assert revenue(client) - before == pytest.approx(40 * IMAGE, abs=1)


def test_reverse_return_removes_amounts_and_team_can_resubmit(client):
    before = revenue(client)
    rid, _ = submit(client)
    out = client.post(f'/api/reports/{rid}/return', json={'comment': '井号报错', 'reverse': True}, headers=FIN).json()
    assert out['status'] == '驳回' and out['batch']['reversed'] > 0
    assert revenue(client) == pytest.approx(before, abs=1)                                     # 金额立即冲销
    client.put(f'/api/reports/{rid}', json=body(4870), headers=TEAM)
    assert client.post(f'/api/reports/{rid}/submit', headers=TEAM).json()['status'] == '已审核'
    assert revenue(client) - before == pytest.approx(46 * IMAGE, abs=1)


def test_legacy_submitted_report_confirmed_by_management(client):
    from app import reports
    from app.db import connect
    rid = client.post('/api/reports', json=body(), headers=TEAM).json()['report_id']
    c = connect()   # 模拟旧流程遗留：已提交、待部室审核
    c.execute("UPDATE daily_report SET status='已提交' WHERE report_id=?", (rid,))
    reports.facts_from_report(c, rid, '已提交')
    c.commit()
    c.close()
    assert rid in ids(client, 'todo')
    out = client.post(f'/api/reports/{rid}/confirm', json={}, headers=FIN).json()
    assert out['status'] == '已审核' and out['batch']['created'] > 0


# ---------- 规则：影响预览、变化依据、回滚 ----------

def _mud_v2(client, price):
    mud = next(r for r in client.get('/api/rules', headers=FIN).json() if r['rule_code'] == 'RL-C2-1-02-01')
    nid = client.post(f"/api/rules/{mud['rule_id']}/new-version", json={'valid_from': '2026-08-01'}, headers=FIN).json()['rule']['rule_id']
    client.put(f'/api/rules/{nid}', json={'unit_price': price}, headers=FIN)
    assert client.post(f'/api/rules/{nid}/confirm', headers=FIN).json()['rule']['status'] == '待发布'
    return mud['rule_id'], nid


def _counts():
    from app.db import connect
    c = connect()
    n = tuple(c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('economic_event', 'calc_batch', 'approval_log'))
    st = c.execute("SELECT status FROM economic_rule WHERE rule_code='RL-C2-1-02-01' ORDER BY version").fetchall()
    c.close()
    return n, [r[0] for r in st]


def test_impact_preview_publish_and_rollback(client):
    old, nid = _mud_v2(client, 420)                     # 415.35 → 420，回算偏差 1.1%，达标
    cost0 = client.get('/api/wells/W001/result?period=2026-08', headers=FIN).json()['kpi']['total_cost']
    snap = _counts()
    imp = client.post(f'/api/rules/{nid}/impact', headers=FIN).json()
    assert _counts() == snap                            # 预览整体回滚，不落库
    assert imp['reversed'] > 0 and imp['well_count'] >= 1 and imp['amount_change'] != 0
    d = client.post(f'/api/rules/{nid}/publish', json={'change_basis': '工程技术部 泥浆服务调价通知 2026-08'}, headers=FIN).json()
    assert d['rule']['status'] == '已发布' and d['batch']['amount_change'] == imp['amount_change']   # 预览与实际一致
    cost1 = client.get('/api/wells/W001/result?period=2026-08', headers=FIN).json()['kpi']['total_cost']
    assert cost1 != cost0
    # 发布后发现有误：回滚，v2 废止、v1 恢复，金额回到原值
    assert client.post(f'/api/rules/{nid}/rollback', json={'comment': ''}, headers=FIN).status_code == 422
    r = client.post(f'/api/rules/{nid}/rollback', json={'comment': '调价通知尚未生效'}, headers=FIN).json()
    assert r['restored'] == 1 and r['rule']['rule_id'] == old and r['rule']['status'] == '已发布' and r['rule']['valid_to'] is None
    assert _counts()[1] == ['已发布', '已废止']
    cost2 = client.get('/api/wells/W001/result?period=2026-08', headers=FIN).json()['kpi']['total_cost']
    assert cost2 == pytest.approx(cost0, abs=1)
    assert client.post(f'/api/rules/{nid}/rollback', json={'comment': '再回滚'}, headers=FIN).status_code == 409


def test_rollback_without_previous_version_disables_rule(client):
    draft = next(r for r in client.get('/api/rules', headers=FIN).json() if r['rule_code'] == 'RL-C2-3-04')
    client.post(f"/api/rules/{draft['rule_id']}/confirm", headers=FIN)
    client.post(f"/api/rules/{draft['rule_id']}/publish", json={'change_basis': '定向服务合同 2026-001'}, headers=FIN)
    r = client.post(f"/api/rules/{draft['rule_id']}/rollback", json={'comment': '合同未签'}, headers=FIN).json()
    assert r['restored'] is None and r['rule']['status'] == '已废止'
    rep = client.get('/api/reports/current?well_id=W001&date=2026-10-10', headers=TEAM).json()
    assert not next(x for x in rep['line_defs'] if x['key'] == 'C2-3-04')['show']     # 停用后日报不再列这一项
