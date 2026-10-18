"""规则引擎：已审核事实 × 已发布规则 → 经济事项。

计算顺序：收入（calc_order 1）→ 直接成本（2）→ 比例计提（3）。
重算时先算出“应有的事项”，再与现有有效事项逐笔比对：金额不变的保留，
变化或不再适用的生成负数冲销记录，新增的写入新记录。事项只冲不改。
"""
import calendar
from collections import defaultdict

from . import report_lines
from .db import jdump, jload, log, money, now

ENGINE_VERSION = 'engine-1.0'
ENGINE_ORIGINS = ('规则计算', '比例计提')


# ---------- 单井基础信息与条件匹配 ----------

def well_context(conn, well_id: str, date: str, team_id: str | None = None, depth: float | None = None) -> dict:
    """规则适用条件取值：单井基础信息 + 当日所在开次与队伍。"""
    w = conn.execute('SELECT * FROM well WHERE well_id=?', (well_id,)).fetchone()
    seg = conn.execute(
        'SELECT p.phase, t.team_no, t.rig_type, t.team_id FROM team_well_period p JOIN team t ON t.team_id=p.team_id '
        'WHERE p.well_id=? AND p.start_at<=? AND (p.end_at IS NULL OR p.end_at>=?) '
        + ('AND p.team_id=? ' if team_id else '') + 'ORDER BY p.start_at DESC LIMIT 1',
        (well_id, date, date) + ((team_id,) if team_id else ())).fetchone()
    return {
        '井号': well_id, '设计井深': w['design_depth'] or 0,
        '市场': w['market'], '区块': w['block'], '井别': w['well_category'], '井型': w['well_type'],
        '甲方': w['client'] or '',
        '开次': seg['phase'] if seg else '', '队伍': seg['team_no'] if seg else '',
        '钻机型号': seg['rig_type'] if seg else '',
        '井深': depth if depth is not None else w['actual_depth'],
    }


def cond_match(ctx: dict, dimension: str, operator: str, value: str) -> bool:
    actual = ctx.get(dimension)
    if actual is None:
        return False
    if operator == '等于':
        return str(actual) == value
    if operator == '属于':
        return str(actual) in [v.strip() for v in value.replace('，', ',').split(',')]
    try:
        a, v = float(actual), float(value)
    except (TypeError, ValueError):
        return False
    return {'大于': a > v, '小于': a < v, '不小于': a >= v, '不大于': a <= v}.get(operator, False)


def rule_conditions(conn, rule_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        'SELECT dimension, operator, value FROM rule_condition WHERE rule_id=? ORDER BY id', (rule_id,))]


def factor_of(rule: dict, ctx: dict) -> tuple[float, list[str]]:
    """calc_params.factor_rules：满足条件时乘系数，如 井深 > 5000 米 系数 1.2。"""
    params = jload(rule['calc_params'])
    f, notes = 1.0, []
    for fr in params.get('factor_rules', []):
        if cond_match(ctx, fr['dimension'], fr['operator'], str(fr['value'])):
            f *= float(fr['factor'])
            notes.append(f"{fr['dimension']}{fr['operator']}{fr['value']} → ×{fr['factor']}")
    return f, notes


def published_rules(conn, date: str, calc_orders: tuple) -> list[dict]:
    q = ('SELECT * FROM economic_rule WHERE status IN (\'已发布\', \'已被替代\') AND valid_from<=? '
         'AND (valid_to IS NULL OR valid_to>=?) AND calc_order IN (%s) ORDER BY calc_order, priority DESC, version DESC'
         % ','.join('?' * len(calc_orders)))
    return [dict(r) for r in conn.execute(q, (date, date) + calc_orders)]


def match_fact(conn, fact: dict, rules: list[dict], used: dict | None = None) -> list[tuple[dict, dict]]:
    """返回命中的 (规则, 计算明细)。同一费用项目内：互斥取优先级最高的一条，叠加的全部生效。

    单井合同优先：某科目有带“井号”条件且适用于本井的规则时，同科目不带井号的规则（框架合同）对本井不生效，
    避免单井总价与框架日费 / 米费重复确认收入。used 记录包干规则已确认的累计金额，供封顶（同一次重算内共享）。"""
    attrs = jload(fact['attributes'])
    ctx = well_context(conn, fact['well_id'], fact['fact_date'], fact['team_id'], attrs.get('depth_end'))
    conds_of = {r['rule_id']: rule_conditions(conn, r['rule_id']) for r in rules}
    applies = {r['rule_id']: all(cond_match(ctx, c['dimension'], c['operator'], c['value']) for c in conds_of[r['rule_id']]) for r in rules}
    pinned = {r['item_code'] for r in rules if applies[r['rule_id']] and any(c['dimension'] == '井号' for c in conds_of[r['rule_id']])}
    by_item = defaultdict(list)
    for r in rules:
        if r['fact_type'] != fact['fact_type'] or not applies[r['rule_id']]:
            continue
        conds = conds_of[r['rule_id']]
        if r['item_code'] in pinned and not any(c['dimension'] == '井号' for c in conds):
            continue
        line = report_lines.rule_line(r)
        if line and report_lines.canon(attrs.get('line')) != line:
            continue   # 规则指定了报量项目时只取该项目的量；未指定（如伙食费）取该类事实全部
        by_item[r['item_code']].append((r, conds))
    hits = []
    for item, cand in by_item.items():
        exclusive = [x for x in cand if x[0]['combine_mode'] == '互斥']
        chosen = exclusive[:1] + [x for x in cand if x[0]['combine_mode'] == '叠加']
        for r, conds in chosen:
            f, notes = factor_of(r, ctx)
            snap = {'conditions': [f"{c['dimension']} {c['operator']} {c['value']}" for c in conds],
                    'context': {k: ctx[k] for k in ('市场', '区块', '井型', '开次', '队伍')}, 'factor_notes': notes}
            if is_lump_sum(r):
                c = _lump_sum(conn, fact, r, ctx, f, used if used is not None else {})
                if c is None:
                    continue
                snap.update(c.pop('snapshot'))
                hits.append((r, {**c, 'snapshot': snap}))
                continue
            amount = money(fact['quantity'] * r['unit_price'] * f)
            snap['formula'] = f"{fmt_qty(fact['quantity'])} {fact['unit']} × {r['unit_price']:,.4f} 元/{r['unit']} × {f:.2f}"
            hits.append((r, {'quantity': fact['quantity'], 'unit_price': r['unit_price'], 'factor': f, 'amount': amount,
                             'snapshot': snap}))
    return hits


def is_lump_sum(rule: dict) -> bool:
    """单井固定总价 / 总包：包干规则按进尺确认，unit_price 为合同总价。"""
    return rule['pricing_method'] == '包干' and rule['fact_type'] == '进尺'


def _lump_sum(conn, fact: dict, r: dict, ctx: dict, f: float, used: dict) -> dict | None:
    """米价 = 合同总价 ÷ 设计井深；当日金额 = 进尺 × 米价 × 系数，累计封顶为合同总价（超深部分不再确认）。"""
    depth = float(ctx.get('设计井深') or 0)
    if depth <= 0:
        return None   # 没有设计井深无法折算米价：不确认收入，规则详情与单井页可看到该井未计
    total = r['unit_price']
    key = (r['item_code'], fact['well_id'])
    if key not in used:   # 单井合同覆盖整口井：以前月份本井该科目已确认的全部金额（含历史导入；不含本月，本月正在重算）
        used[key] = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM economic_event WHERE item_code=? AND well_id=? AND period<? AND status='有效'",
            (r['item_code'], fact['well_id'], fact['fact_date'][:7])).fetchone()[0]
    per_m = total / depth
    raw = money(fact['quantity'] * per_m * f)
    amount = money(max(0.0, min(raw, total - used[key])))
    if amount <= 0 and raw > 0:
        return None   # 已封顶
    used[key] += amount
    formula = (f"{fmt_qty(fact['quantity'])} 米 × {per_m:,.4f} 元/米（合同总价 {total:,.0f} 元 ÷ 设计井深 {fmt_qty(depth)} 米）"
               + (f" × {f:.2f}" if f != 1 else ''))
    if amount < raw:
        formula += f"；累计封顶为合同总价，本次只确认 {amount:,.0f} 元"
    return {'quantity': fact['quantity'], 'unit_price': round(per_m, 4), 'factor': f, 'amount': amount,
            'snapshot': {'formula': formula, 'lump_sum': {'total': total, 'design_depth': depth, 'capped': amount < raw}}}


def fmt_qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else f'{q:g}'


def period_end(period: str) -> str:
    y, m = map(int, period.split('-'))
    return f'{period}-{calendar.monthrange(y, m)[1]:02d}'


# ---------- 重算 ----------

def _desired(conn, well_id: str, period: str) -> dict:
    """算出某井某月“应有的”引擎事项，键为 (类别, 来源, 规则)。"""
    item = {r['item_code']: dict(r) for r in conn.execute('SELECT * FROM cost_item')}
    # 已导入历史数据或已分摊的科目以导入 / 分摊金额为准，规则不再重复计算
    imported = {r['item_code'] for r in conn.execute(
        "SELECT DISTINCT item_code FROM economic_event WHERE well_id=? AND period=? AND origin_type IN ('历史导入', '分摊') "
        "AND status='有效'",
        (well_id, period))}
    out = {}
    facts = [dict(r) for r in conn.execute(
        "SELECT * FROM business_fact WHERE well_id=? AND substr(fact_date,1,7)=? AND status='已审核' AND scenario='实际' "
        'ORDER BY fact_date, fact_id', (well_id, period))]
    used: dict = {}   # 包干规则累计已确认金额（按日期顺序累加，用于封顶）
    for fact in facts:
        for r, c in match_fact(conn, fact, published_rules(conn, fact['fact_date'], (1, 2)), used):
            if r['item_code'] in imported:
                continue
            it = item[r['item_code']]
            out[('fact', fact['fact_id'], r['rule_id'])] = dict(
                event_date=fact['fact_date'], team_id=fact['team_id'], item_code=r['item_code'],
                direction=it['direction'], cost_behavior=it['cost_behavior'], owner_dept=it['owner_dept'],
                origin_type='规则计算', fact_id=fact['fact_id'], base_event_ids=None, rule_id=r['rule_id'],
                quantity=c['quantity'], unit_price=c['unit_price'], factor=c['factor'], amount=c['amount'],
                calc_snapshot=c['snapshot'])

    # 比例计提：基数 = 本月基数项目的全部事项（含历史导入）+ 本次应生成的收入事项
    end = period_end(period)
    for r in published_rules(conn, end, (3,)):
        if not r['base_item_code'] or r['item_code'] in imported:
            continue
        existing = conn.execute(
            "SELECT event_id, amount FROM economic_event WHERE well_id=? AND period=? AND item_code=? "
            "AND origin_type NOT IN ('规则计算', '比例计提')", (well_id, period, r['base_item_code'])).fetchall()
        base = sum(e['amount'] for e in existing)
        base_ids = [e['event_id'] for e in existing]
        for v in out.values():
            if v['item_code'] == r['base_item_code']:
                base += v['amount']
        if money(base) == 0:
            continue
        ctx = well_context(conn, well_id, end)
        conds = rule_conditions(conn, r['rule_id'])
        if not all(cond_match(ctx, c['dimension'], c['operator'], c['value']) for c in conds):
            continue
        it = item[r['item_code']]
        out[('ratio', period, r['rule_code'])] = dict(
            event_date=end, team_id=None, item_code=r['item_code'], direction=it['direction'],
            cost_behavior=it['cost_behavior'], owner_dept=it['owner_dept'], origin_type='比例计提', fact_id=None,
            base_event_ids=jdump(base_ids), rule_id=r['rule_id'], quantity=money(base), unit_price=r['unit_price'],
            factor=1.0, amount=money(base * r['unit_price']),
            calc_snapshot={'formula': f"{item[r['base_item_code']]['item_name']} {base:,.0f} 元 × {r['unit_price'] * 100:.2f}%",
                           'base_item': r['base_item_code'], 'conditions': [f"{c['dimension']} {c['operator']} {c['value']}" for c in conds]})
    return out


def _key_of(ev) -> tuple:
    if ev['origin_type'] == '比例计提':
        code = ev['rule_code']
        return ('ratio', ev['period'], code)
    return ('fact', ev['fact_id'], ev['rule_id'])


def recalc(conn, well_ids: list[str], periods: list[str], trigger: str, operator: str) -> dict:
    batch_id = conn.execute(
        'INSERT INTO calc_batch (trigger, scope, engine_version, started_at, operator) VALUES (?,?,?,?,?)',
        (trigger, jdump({'wells': well_ids, 'periods': periods}), ENGINE_VERSION, now(), operator)).lastrowid
    created = reversed_n = 0
    change = 0.0
    gone = {r['well_id'] for r in conn.execute('SELECT well_id FROM well_deleted')}   # 已删除的井不再计算
    for well_id in well_ids:
        if well_id in gone:
            continue
        for period in periods:
            desired = _desired(conn, well_id, period)
            current = conn.execute(
                "SELECT e.*, r.rule_code FROM economic_event e LEFT JOIN economic_rule r ON r.rule_id=e.rule_id "
                "WHERE e.well_id=? AND e.period=? AND e.status='有效' AND e.origin_type IN ('规则计算', '比例计提')",
                (well_id, period)).fetchall()
            for ev in current:
                k = _key_of(ev)
                want = desired.get(k)
                if want and want['rule_id'] == ev['rule_id'] and want['amount'] == ev['amount']:
                    desired.pop(k)
                    continue
                reverse_event(conn, ev, batch_id)
                reversed_n += 1
                change -= ev['amount']
            for v in desired.values():
                conn.execute(
                    'INSERT INTO economic_event (scenario, event_date, period, well_id, team_id, item_code, direction, '
                    'cost_behavior, owner_dept, origin_type, fact_id, base_event_ids, rule_id, quantity, unit_price, factor, '
                    "amount, calc_snapshot, batch_id, status) VALUES ('实际',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, '有效')",
                    (v['event_date'], period, well_id, v['team_id'], v['item_code'], v['direction'], v['cost_behavior'],
                     v['owner_dept'], v['origin_type'], v['fact_id'], v['base_event_ids'], v['rule_id'], v['quantity'],
                     v['unit_price'], v['factor'], v['amount'], jdump(v['calc_snapshot']), batch_id))
                created += 1
                change += v['amount']
    conn.execute('UPDATE calc_batch SET finished_at=?, event_count=?, amount_change=? WHERE batch_id=?',
                 (now(), created + reversed_n, money(change), batch_id))
    log(conn, '计算批次', batch_id, trigger, operator, f'新增 {created} 笔，冲销 {reversed_n} 笔')
    return {'batch_id': batch_id, 'created': created, 'reversed': reversed_n, 'amount_change': money(change)}


def reverse_event(conn, ev, batch_id: int) -> None:
    conn.execute("UPDATE economic_event SET status='已冲销' WHERE event_id=?", (ev['event_id'],))
    conn.execute(
        'INSERT INTO economic_event (scenario, event_date, period, well_id, team_id, item_code, direction, cost_behavior, '
        'owner_dept, origin_type, fact_id, base_event_ids, rule_id, quantity, unit_price, factor, amount, calc_snapshot, '
        "batch_id, status, reversal_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, '冲销', ?)",
        (ev['scenario'], ev['event_date'], ev['period'], ev['well_id'], ev['team_id'], ev['item_code'], ev['direction'],
         ev['cost_behavior'], ev['owner_dept'], ev['origin_type'], ev['fact_id'], ev['base_event_ids'], ev['rule_id'],
         ev['quantity'], ev['unit_price'], ev['factor'], -ev['amount'], ev['calc_snapshot'], batch_id, ev['event_id']))


def affected_scope(conn, item_code: str | None = None) -> tuple[list[str], list[str]]:
    """有已审核事实或已有事项的全部井与月份。"""
    wells = set()
    periods = set()
    for r in conn.execute("SELECT DISTINCT well_id, substr(fact_date,1,7) p FROM business_fact WHERE status='已审核'"):
        wells.add(r['well_id']); periods.add(r['p'])
    for r in conn.execute('SELECT DISTINCT well_id, period p FROM economic_event'):
        wells.add(r['well_id']); periods.add(r['p'])
    wells -= {r['well_id'] for r in conn.execute('SELECT well_id FROM well_deleted')}
    return sorted(wells), sorted(periods)


# ---------- 回算测试 ----------

def backtest(conn, rule_id: int, threshold: float = 0.05) -> dict:
    """用已有历史导入金额的井回算：规则算出的金额与总表金额逐井比对。"""
    rule = dict(conn.execute('SELECT * FROM economic_rule WHERE rule_id=?', (rule_id,)).fetchone())
    conn.execute('DELETE FROM rule_test WHERE rule_id=?', (rule_id,))
    samples = conn.execute(
        "SELECT well_id, period, SUM(amount) amt FROM economic_event WHERE item_code=? AND origin_type='历史导入' "
        'GROUP BY well_id, period', (rule['item_code'],)).fetchall()
    if not samples:
        # 没有历史导入金额时，以现行已发布版本算出的金额为对照（如调价新版本）
        samples = conn.execute(
            "SELECT well_id, period, SUM(amount) amt FROM economic_event WHERE item_code=? AND origin_type='规则计算' "
            'AND rule_id!=? GROUP BY well_id, period HAVING ABS(SUM(amount))>=0.5', (rule['item_code'], rule_id)).fetchall()
    results = []
    for s in samples:
        facts = [dict(r) for r in conn.execute(
            "SELECT * FROM business_fact WHERE well_id=? AND substr(fact_date,1,7)=? AND status='已审核' AND fact_type=?",
            (s['well_id'], s['period'], rule['fact_type'] or ''))]
        if not facts:
            continue
        calc_amt = 0
        used: dict = {}
        for f in sorted(facts, key=lambda x: (x['fact_date'], x['fact_id'])):
            for r, c in match_fact(conn, f, [rule], used):
                calc_amt += c['amount']
        dev = (calc_amt - s['amt']) / s['amt'] if s['amt'] else (0.0 if calc_amt == 0 else 1.0)
        res = '通过' if abs(dev) <= threshold else '不通过'
        conn.execute(
            'INSERT INTO rule_test (rule_id, well_id, period, expected_amount, calc_amount, deviation_rate, result, tested_at) '
            'VALUES (?,?,?,?,?,?,?,?)', (rule_id, s['well_id'], s['period'], money(s['amt']), money(calc_amt), dev, res, now()))
        results.append({'well_id': s['well_id'], 'period': s['period'], 'expected': money(s['amt']), 'calc': money(calc_amt),
                        'deviation': dev, 'result': res})
    if not results:
        conn.execute(
            "INSERT INTO rule_test (rule_id, result, tested_at) VALUES (?, '豁免', ?)", (rule_id, now()))
        return {'result': '豁免', 'samples': [], 'message': '无可回算的历史井，按规定走豁免审批'}
    ok = all(r['result'] == '通过' for r in results)
    return {'result': '通过' if ok else '不通过', 'samples': results,
            'message': f"{len(results)} 口井回算，{'全部达标' if ok else '存在超阈值偏差'}（阈值 {threshold:.0%}）"}
