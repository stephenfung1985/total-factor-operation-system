"""形象收入计价模式：单井总价 / 总包按 总价 ÷ 设计井深 折算米价，逐日按进尺确认、累计封顶；单井合同优先于框架合同。"""
import pytest

from app import engine
from app.db import transaction
from app.seed import _rule


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()


def _lump(c, well, total, code='RL-R02-W'):
    return _rule(c, code, f'{well} 单井总价', 'R02', None, '进尺', '包干', total, '井', 1, '2026-10-01', '已发布', '人工录入',
                 conds=[('井号', '等于', well)], params={'contract_mode': '单井总价'})


def _r02(c, well, period):
    rows = c.execute("SELECT e.amount, r.rule_code, e.calc_snapshot FROM economic_event e LEFT JOIN economic_rule r ON r.rule_id=e.rule_id "
                     "WHERE e.well_id=? AND e.period=? AND e.item_code='R02' AND e.status='有效'", (well, period)).fetchall()
    return sum(r['amount'] for r in rows), {r['rule_code'] for r in rows}, rows


def _recalc(c, well='W001'):
    engine.recalc(c, [well], ['2026-10'], '测试', '测试')


def test_lump_sum_per_meter_and_overrides_framework(db):
    with transaction() as c:
        before, codes, _ = _r02(c, 'W001', '2026-10')
        assert codes == {'RL-R02'} and before > 0            # 框架合同按米费确认
        _lump(c, 'W001', 9_000_000)                          # 8 月已确认 7,147,829 元，余额足够
        _recalc(c)
        amt, codes, rows = _r02(c, 'W001', '2026-10')
        foot = c.execute("SELECT SUM(quantity) FROM business_fact WHERE well_id='W001' AND fact_type='进尺' AND status='已审核' "
                         "AND substr(fact_date,1,7)='2026-10'").fetchone()[0]
    assert codes == {'RL-R02-W'}                             # 单井合同优先，框架米费不再对该井生效
    assert amt == pytest.approx(foot * 9_000_000 / 5200, abs=len(rows))   # 设计井深 5200 米，逐日取整
    assert '合同总价 9,000,000 元 ÷ 设计井深 5200 米' in rows[0]['calc_snapshot']


def test_lump_sum_caps_at_contract_total(db):
    with transaction() as c:
        _lump(c, 'W001', 7_500_000)
        _recalc(c)
        amt, _, rows = _r02(c, 'W001', '2026-10')
        prior = c.execute("SELECT SUM(amount) FROM economic_event WHERE well_id='W001' AND item_code='R02' AND period<'2026-10' "
                          "AND status='有效'").fetchone()[0]
    assert amt == pytest.approx(7_500_000 - prior, abs=1)   # 累计封顶为合同总价
    assert any('封顶' in r['calc_snapshot'] for r in rows)
    with transaction() as c:                                 # 重算不改变结果（事项只冲不改，金额不变的保留）
        _recalc(c)
        assert _r02(c, 'W001', '2026-10')[0] == amt


def test_other_wells_contract_does_not_pin_this_well(db):
    with transaction() as c:
        before = _r02(c, 'W001', '2026-10')[0]
        _lump(c, 'W002', 6_000_000)
        _recalc(c)
        amt, codes, _ = _r02(c, 'W001', '2026-10')
    assert codes == {'RL-R02'} and amt == before


def test_lump_sum_without_design_depth_recognizes_nothing(db):
    with transaction() as c:
        c.execute("UPDATE well SET design_depth=0 WHERE well_id='W001'")
        _lump(c, 'W001', 9_000_000)
        _recalc(c)
        assert _r02(c, 'W001', '2026-10')[0] == 0
