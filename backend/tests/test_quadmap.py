"""四维地图：分类边界、两率同时参与、加权汇总、零/负收入、战略标记、越权、迁移幂等。"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.quadmap import REFERENCE, advice, classify, quality_of, rates_from_sums, validate_thresholds, weighted_rates


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


def H(user):
    return {'X-User-Id': user}


FIN, TEAM, ADMIN, COST, DEPT = H('u_fin'), H('u_team'), H('u_admin'), H('u_cost'), H('gongcheng')

# 盈利线 5% / 10%，微利线 0% / 0%。两项都必须过线。
CUSTOM = {'profit_net_min': 0.05, 'profit_gm_min': 0.10, 'thin_net_min': 0.0, 'thin_gm_min': 0.0}


def test_reference_boundaries_use_both_rates():
    """初始参考是会计零线：覆盖总成本且覆盖变动成本才是盈利；只覆盖变动成本是微利；毛益率为负是亏损。"""
    ok = 'ok'
    assert classify(0, 0, ok, REFERENCE) == '盈利'                  # 两条线都踩在边界上
    assert classify(0.2, 0.3, ok, REFERENCE) == '盈利'
    assert classify(-0.0001, 0, ok, REFERENCE) == '微利'            # 利润率掉到线下，毛益率仍过线
    assert classify(-0.2, 0.4, ok, REFERENCE) == '微利'             # 毛益率高也不能单独判盈利
    assert classify(0.5, -0.0001, ok, REFERENCE) == '亏损'          # 利润率高，毛益率为负，仍是亏损
    assert classify(-0.2, -0.1, ok, REFERENCE) == '亏损'
    assert classify(None, 0.4, ok, REFERENCE) == '不可判定'
    assert classify(0.2, 0.4, 'revenue_non_positive', REFERENCE) == '不可判定'
    assert classify(0.2, 0.4, 'no_data', REFERENCE) == '不可判定'
    assert classify(0.2, 0.4, 'cost_unrecorded', REFERENCE) == '不可判定'


def test_custom_thresholds_each_axis_can_flip_the_class():
    assert classify(0.05, 0.10, 'ok', CUSTOM) == '盈利'
    assert classify(0.049, 0.10, 'ok', CUSTOM) == '微利'            # 只把利润率降到盈利线下
    assert classify(0.05, 0.099, 'ok', CUSTOM) == '微利'            # 只把毛益率降到盈利线下
    assert classify(-0.001, 0.50, 'ok', CUSTOM) == '亏损'           # 毛益率很高，利润率低于微利线
    assert classify(0.20, -0.001, 'ok', CUSTOM) == '亏损'           # 利润率很高，毛益率低于微利线
    assert classify(0, 0, 'ok', CUSTOM) == '微利'                   # 踩在微利线上，还没到盈利线


def test_equal_lines_are_rejected_and_rates_are_not_invented_at_zero():
    with pytest.raises(HTTPException) as exc:
        validate_thresholds({'profit_net_min': 0, 'profit_gm_min': 0, 'thin_net_min': 0, 'thin_gm_min': 0})
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        validate_thresholds({'profit_net_min': 0, 'profit_gm_min': -0.1, 'thin_net_min': None, 'thin_gm_min': 0})
    assert quality_of(0, True, True) == 'revenue_non_positive'
    assert quality_of(-10, True, True) == 'revenue_non_positive'
    assert quality_of(100, False, False) == 'no_data'
    assert quality_of(100, True, False) == 'cost_unrecorded'
    assert rates_from_sums(0, -5, -5, 'revenue_non_positive') == (None, None)
    assert rates_from_sums(-10, -15, -20, 'revenue_non_positive') == (None, None)
    assert rates_from_sums(100, 100, 100, 'cost_unrecorded') == (None, None)


def test_weighted_rate_is_not_the_average_of_well_rates():
    """一口井 100% 、一口井 0%，收入 100 和 300。简单平均是 50%，按金额合计是 25%。"""
    out = weighted_rates([(100, 100, 100), (300, 0, 0)])
    assert out['gm_rate'] == pytest.approx(0.25)
    assert out['net_rate'] == pytest.approx(0.25)
    assert out['avg_gm_rate'] == pytest.approx(0.5)
    assert out['gm_rate'] != pytest.approx(out['avg_gm_rate'])


def test_strategic_loss_advice_keeps_the_loss_visible():
    strategy = {'active': True, 'review_due': '2020-01-01', 'risk_limit': '不新增钻机，只评估现有一台是否保留', 'overdue': True}
    text = advice('战略', '亏损', 'ok', {**REFERENCE, 'confirmed': False}, strategy, -0.2, -0.1)['text']
    assert '亏损' in text and '不建议新增钻机' in text and '已过' in text
    assert '无限' not in text
    bad = advice('不可判定', '不可判定', 'revenue_non_positive', REFERENCE, None, None, None)
    assert bad['code'] == 'insufficient' and '不计算 0%' in bad['text'] and '优先保障' not in bad['text']


def _leaf(conn, root: str) -> str:
    rows = conn.execute('SELECT item_code, parent_code FROM cost_item').fetchall()
    children: dict[str, list[str]] = {}
    for row in rows:
        children.setdefault(row['parent_code'], []).append(row['item_code'])
    stack, leaves = [root], []
    while stack:
        code = stack.pop()
        kids = children.get(code, [])
        if kids:
            stack.extend(kids)
        elif code != root:
            leaves.append(code)
    assert leaves, root
    return leaves[0]


def _well(conn, wid: str, block: str) -> None:
    conn.execute(
        'INSERT INTO well (well_id, market, block, well_category, well_type, spud_sections, design_depth, actual_depth, '
        "client, contract_amount, status) VALUES (?, '西北', ?, '开发井', '直井', 2, 1000, 0, '', 0, '正钻')",
        (wid, block))


def _event(conn, wid: str, code: str, direction: str, amount: float) -> None:
    conn.execute(
        'INSERT INTO economic_event (event_date, period, well_id, item_code, direction, origin_type, amount, status) '
        "VALUES ('2026-01-31', '2026-01', ?, ?, ?, '历史导入', ?, '有效')",
        (wid, code, direction, amount))


def _money(conn, wid: str, revenue: float, variable: float, fixed: float, fixed_code: str, var_code: str) -> None:
    _event(conn, wid, 'R02', '收入', revenue)
    _event(conn, wid, var_code, '成本', variable)
    _event(conn, wid, fixed_code, '成本', fixed)


def _block(body, name):
    return next(b for b in body['blocks'] if b['block'] == name)


def test_map_classification_weighting_and_missing_values(client):
    from app.db import connect
    conn = connect()
    fixed_code, var_code = _leaf(conn, 'C1'), _leaf(conn, 'C2')
    # 收入 1000。盈利：毛益 100、净利 50。微利各偏一条线。亏损各低于微利线。另有零收入、负收入、无成本、无事项。
    cases = [
        ('QP', '边界盈利', 1000, 900, 50),
        ('QN', '边界微利净', 1000, 900, 51),
        ('QG', '边界微利毛', 1000, 901, 49),
        ('QL', '边界亏损净', 1000, 500, 501),
        ('QM', '边界亏损毛', 1000, 1001, -201),
        ('WA', '测试加权', 100, 0, 0),
        ('WB', '测试加权', 300, 300, 0),
        ('QZ', '零收入', 0, 100, 10),
        ('QF', '负收入', -10, 5, 1),
    ]
    for wid, block, revenue, variable, fixed in cases:
        _well(conn, wid, block)
        _money(conn, wid, revenue, variable, fixed, fixed_code, var_code)
    _well(conn, 'QC', '有收入无成本')
    _event(conn, 'QC', 'R02', '收入', 1000)
    _well(conn, 'QD', '完全没有事项')
    conn.commit()
    conn.close()

    saved = client.put('/api/quad-map/thresholds', json={**CUSTOM, 'confirm': False}, headers=FIN)
    assert saved.status_code == 200
    assert saved.json()['official'] is False and saved.json()['state'] == 'draft'
    body = client.get('/api/quad-map?period=2026-01', headers=FIN).json()
    assert body['formula']['gm_rate'].startswith('毛益率 = 区块毛益额合计')
    assert 'R02' in body['formula']['revenue'] and 'C2' in body['formula']['variable']
    assert '待经营管理确认' in body['thresholds']['state_label']

    assert _block(body, '边界盈利')['financial_class'] == '盈利'
    assert _block(body, '边界盈利')['quadrant_label'].startswith('参考·')
    assert _block(body, '边界微利净')['financial_class'] == '微利'
    assert _block(body, '边界微利毛')['financial_class'] == '微利'
    assert _block(body, '边界亏损净')['financial_class'] == '亏损'
    assert _block(body, '边界亏损毛')['financial_class'] == '亏损'
    # 只改一个轴就从盈利落到微利或亏损，说明两项都参与了
    assert _block(body, '边界微利净')['net_rate'] == pytest.approx(0.049)
    assert _block(body, '边界微利毛')['gm_rate'] == pytest.approx(0.099)

    weighted = _block(body, '测试加权')
    assert weighted['revenue'] == 400
    assert weighted['gm_rate'] == pytest.approx(0.25)
    assert weighted['net_rate'] == pytest.approx(0.25)
    assert weighted['well_count'] == 2

    for name in ('零收入', '负收入', '有收入无成本', '完全没有事项'):
        item = _block(body, name)
        assert item['gm_rate'] is None and item['net_rate'] is None
        assert item['financial_class'] == '不可判定'
        assert item['advice']['code'] in ('insufficient', 'threshold_unset')
        assert '优先保障' not in item['advice']['text']
        assert item['rig_count'] is None and item['budget'] is None
    assert _block(body, '零收入')['quality'] == 'revenue_non_positive'
    assert _block(body, '负收入')['quality'] == 'revenue_non_positive'
    assert _block(body, '有收入无成本')['quality'] == 'cost_unrecorded'
    assert _block(body, '完全没有事项')['quality'] == 'no_data'


def test_strategy_mark_clear_and_permission(client):
    loss = {
        'block': '边界亏损净', 'rationale': '该区矿权将于两年内到期，需保留一口评价井',
        'review_due': '2020-01-01', 'risk_limit': '不新增钻机，只评估现有施工是否保留到复评',
    }
    from app.db import connect
    conn = connect()
    fixed_code, var_code = _leaf(conn, 'C1'), _leaf(conn, 'C2')
    _well(conn, 'QL', '边界亏损净')
    _money(conn, 'QL', 1000, 500, 501, fixed_code, var_code)
    conn.commit()
    conn.close()
    client.put('/api/quad-map/thresholds', json={**CUSTOM, 'confirm': True}, headers=FIN)

    assert client.post('/api/quad-map/strategy', json=loss, headers=TEAM).status_code == 403
    assert client.post('/api/quad-map/strategy', json=loss, headers=DEPT).status_code == 403
    assert client.post('/api/quad-map/strategy', json=loss, headers=COST).status_code == 403
    assert client.put('/api/quad-map/thresholds', json=CUSTOM, headers=TEAM).status_code == 403
    assert client.put('/api/quad-map/thresholds', json={**CUSTOM, 'confirm': True}, headers=ADMIN).status_code == 200
    assert client.post('/api/quad-map/strategy', json={**loss, 'block': '不存在的区块'}, headers=FIN).status_code == 404
    assert client.post('/api/quad-map/strategy', json={**loss, 'rationale': '太短'}, headers=FIN).status_code == 422
    assert client.post('/api/quad-map/strategy', json={**loss, 'risk_limit': '无限投入，不设上限'}, headers=FIN).status_code == 422

    marked = client.post('/api/quad-map/strategy', json=loss, headers=FIN)
    assert marked.status_code == 200
    body = client.get('/api/quad-map?period=2026-01', headers=FIN).json()
    item = _block(body, '边界亏损净')
    assert item['quadrant'] == '战略' and item['financial_class'] == '亏损' and item['loss_visible'] is True
    assert '亏损' in item['advice']['text'] and '不建议新增' in item['advice']['text']
    assert item['strategy']['overdue'] is True
    assert item['quadrant_label'] == '战略'          # 已确认阈值后，基础分类不再带“参考”
    assert item['financial_label'] == '亏损'

    updated = client.post('/api/quad-map/strategy', json={**loss, 'review_due': '2027-06-30', 'rationale': '复评后继续保留评价井，观察矿权'}, headers=ADMIN)
    assert updated.status_code == 200 and updated.json()['overdue'] is False

    assert client.post('/api/quad-map/strategy/clear', json={'block': '边界亏损净', 'reason': '矿权论证不足'}, headers=TEAM).status_code == 403
    cleared = client.post('/api/quad-map/strategy/clear', json={'block': '边界亏损净', 'reason': '矿权论证不足'}, headers=FIN)
    assert cleared.status_code == 200
    again = client.get('/api/quad-map?period=2026-01', headers=FIN).json()
    back = _block(again, '边界亏损净')
    assert back['quadrant'] == '亏损' and back['strategy'] is None
    assert client.post('/api/quad-map/strategy/clear', json={'block': '边界亏损净', 'reason': '再取消一次'}, headers=FIN).status_code == 409

    from app.db import connect as connect_again
    conn = connect_again()
    actions = [r['action'] for r in conn.execute(
        "SELECT action FROM approval_log WHERE object_type='区块战略' AND object_id='边界亏损净' ORDER BY id")]
    assert actions == ['标记', '更新', '取消']
    assert conn.execute("SELECT active FROM block_strategy_mark WHERE block='边界亏损净'").fetchone()['active'] == 0
    conn.close()

    withdrawn = client.post('/api/quad-map/thresholds/withdraw', headers=FIN)
    assert withdrawn.status_code == 200 and withdrawn.json()['official'] is False
    assert withdrawn.json()['profit_net_min'] == pytest.approx(0.05)
    again = client.post('/api/quad-map/thresholds/withdraw', headers=FIN)
    assert again.json()['official'] is False
    assert client.post('/api/quad-map/thresholds/withdraw', headers=TEAM).status_code == 403
    conn = connect_again()
    pulls = [r['action'] for r in conn.execute(
        "SELECT action FROM approval_log WHERE object_type='四维地图阈值' AND action='撤回确认'")]
    assert pulls == ['撤回确认']
    conn.close()


def test_team_scope_does_not_include_other_teams_and_dept_is_rejected(client):
    assert client.get('/api/quad-map?period=2026-08', headers=DEPT).status_code == 403
    assert client.get('/api/quad-map?period=2026-08', headers=COST).status_code == 403
    finance = client.get('/api/quad-map?period=2026-08', headers=FIN).json()
    team = client.get('/api/quad-map?period=2026-08', headers=TEAM).json()
    whole = _block(finance, 'A区块')
    own = _block(team, 'A区块')
    assert {w['well_id'] for w in own['wells']} == {'W001'}
    assert own['revenue'] < whole['revenue']
    assert 'B区块' not in {b['block'] for b in team['blocks']}
    assert 'W002' not in {w['well_id'] for b in team['blocks'] for w in b['wells']}
    assert own['rig_count'] is None and '未知' in own['rig_count_note']
    assert own['budget'] is None
    overview = client.get('/api/overview?period=2026-08', headers=FIN).json()
    sums = {}
    for well in overview['wells']:
        bucket = sums.setdefault(well['block'], {'revenue': 0, 'gross_margin': 0, 'net_profit': 0})
        bucket['revenue'] += well['revenue']
        bucket['gross_margin'] += well['gross_margin']
        bucket['net_profit'] += well['net_profit']
    for item in finance['blocks']:
        if item['block'] not in sums:
            continue
        assert item['revenue'] == sums[item['block']]['revenue']
        assert item['gross_margin'] == sums[item['block']]['gross_margin']
        assert item['net_profit'] == sums[item['block']]['net_profit']


def test_navigation_keeps_existing_homes():
    from app.auth import NAV
    assert NAV['finance'][0]['items'][0]['id'] == 'overview'
    assert 'quadmap' in {i['id'] for g in NAV['finance'] for i in g['items']}
    assert NAV['admin'][0]['items'][0]['id'] == 'rules'
    assert [i['id'] for g in NAV['dept'] for i in g['items']] == ['masterdata', 'rules', 'basis']
    assert 'quadmap' not in {i['id'] for g in NAV['dept'] for i in g['items']}
    assert 'quadmap' not in {i['id'] for g in NAV['cost'] for i in g['items']}


def test_threshold_migration_is_idempotent_and_does_not_touch_events(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'migrate.db'))
    from app.db import connect, init_db
    init_db()
    conn = connect()
    assert conn.execute('SELECT COUNT(*) c FROM block_quad_threshold').fetchone()['c'] == 1
    row = conn.execute('SELECT * FROM block_quad_threshold WHERE id=1').fetchone()
    assert row['confirmed'] == 0 and row['profit_net_min'] == 0 and row['profit_gm_min'] == 0
    assert row['thin_net_min'] is None and row['thin_gm_min'] == 0
    events = conn.execute('SELECT COUNT(*) c FROM economic_event').fetchone()['c']
    rules = conn.execute('SELECT COUNT(*) c FROM economic_rule').fetchone()['c']
    conn.execute('UPDATE block_quad_threshold SET confirmed=1, profit_net_min=0.5, updated_by=? WHERE id=1', ('测试',))
    conn.commit()
    conn.close()

    init_db()
    init_db()
    conn = connect()
    assert conn.execute('SELECT COUNT(*) c FROM block_quad_threshold').fetchone()['c'] == 1
    kept = conn.execute('SELECT confirmed, profit_net_min, updated_by FROM block_quad_threshold').fetchone()
    assert kept['confirmed'] == 1 and kept['profit_net_min'] == 0.5 and kept['updated_by'] == '测试'
    assert conn.execute('SELECT COUNT(*) c FROM economic_event').fetchone()['c'] == events
    assert conn.execute('SELECT COUNT(*) c FROM economic_rule').fetchone()['c'] == rules
    conn.close()
