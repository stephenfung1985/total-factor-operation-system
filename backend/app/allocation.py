"""费用分摊：费用池 → 队 → 井。

只对一部分科目开放（折旧、长摊、项目部运行费用、制造费这类月末按财务实际取数的固定成本）：
企划财务部录入费用池金额，选择分摊标准，写入“分摊”类经济事项；尾差归最后一口井。
  队天 / 进尺：月度费用池，按当月已审核事实里各 (队伍, 井) 的标准量比例分到井。
  元/台月：年度费用池，标准 = 费用池金额 ÷ 全年实际钻机台月；各 (队伍, 井, 月) 分得 标准 × 当月钻机台月。
    钻机台月 = 施工时段落在当月的天数 ÷ 当月天数（与费用导出口径一致），全年实际只算到今天为止。
作废时冲销该池全部事项；分摊后该井该月该科目不再由规则重复计算（见 engine._desired）。
"""
import calendar
from datetime import date

from fastapi import HTTPException

from . import engine
from .db import jdump, log, money, now

ALLOCABLE_PREFIXES = ('C1-02', 'C1-03', 'C1-04', 'C1-05')
BASES = {'队天': ('日历天', '队天'), '进尺': ('进尺', '米'), '台月': ('钻机台月', '台月')}
ANNUAL = '台月'   # 年度池：期间为 YYYY


def allocable_items(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT item_code, item_name, owner_dept, parent_code FROM cost_item WHERE is_leaf=1 AND direction='成本' "
                          'ORDER BY sort_no'):
        if any(r['item_code'] == p or r['item_code'].startswith(p + '-') for p in ALLOCABLE_PREFIXES):
            out.append({'item_code': r['item_code'], 'item_name': r['item_name'], 'owner_dept': r['owner_dept']})
    return out


def options(conn) -> dict:
    periods = [r['p'] for r in conn.execute(
        "SELECT DISTINCT substr(fact_date,1,7) p FROM business_fact WHERE status='已审核' ORDER BY p DESC")]
    teams = [dict(r) for r in conn.execute('SELECT team_id, team_no, name FROM team ORDER BY team_no')]
    years = sorted({r['y'] for r in conn.execute('SELECT DISTINCT substr(start_at,1,4) y FROM team_well_period')} | {p[:4] for p in periods},
                   reverse=True)
    return {'items': allocable_items(conn), 'bases': list(BASES), 'periods': periods, 'years': years, 'teams': teams}


def rig_months(conn, year: str, team_id: str | None, today: date | None = None) -> list[dict]:
    """全年实际钻机台月：按 (队伍, 井, 月) 汇总施工时段天数 ÷ 当月天数，只算到今天。"""
    today = today or date.today()
    y = int(year)
    stop = min(date(y, 12, 31), today)
    out: dict[tuple, float] = {}
    q = ('SELECT p.team_id, p.well_id, p.start_at, p.end_at FROM team_well_period p '
         'WHERE p.well_id NOT IN (SELECT well_id FROM well_deleted) ')
    args: list = []
    if team_id:
        q += 'AND p.team_id=? '
        args.append(team_id)
    for sg in conn.execute(q, args):
        a = max(date.fromisoformat(sg['start_at'][:10]), date(y, 1, 1))
        b = min(date.fromisoformat(sg['end_at'][:10]) if sg['end_at'] else stop, stop)
        m = date(a.year, a.month, 1)
        while a <= b and m <= b:
            days_in = calendar.monthrange(m.year, m.month)[1]
            lo, hi = max(a, m), min(b, date(m.year, m.month, days_in))
            if lo <= hi:
                k = (sg['team_id'], sg['well_id'], f'{m.year}-{m.month:02d}')
                out[k] = out.get(k, 0) + ((hi - lo).days + 1) / days_in
            m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return [{'team_id': t, 'well_id': w, 'period': p, 'w': round(v, 6)}
            for (t, w, p), v in sorted(out.items(), key=lambda x: (x[0][2], x[0][1], x[0][0])) if v > 0]


def recipients(conn, period: str, basis: str, team_id: str | None) -> list[dict]:
    if basis == ANNUAL:
        return rig_months(conn, period[:4], team_id)
    fact_type = BASES[basis][0]
    q = ("SELECT f.team_id, f.well_id, SUM(f.quantity) w FROM business_fact f WHERE f.status='已审核' AND f.scenario='实际' "
         'AND substr(f.fact_date,1,7)=? AND f.fact_type=? AND f.well_id NOT IN (SELECT well_id FROM well_deleted) ')
    args = [period, fact_type]
    if team_id:
        q += 'AND f.team_id=? '
        args.append(team_id)
    q += 'GROUP BY f.team_id, f.well_id HAVING SUM(f.quantity)>0 ORDER BY f.well_id, f.team_id'
    return [{**dict(r), 'period': period} for r in conn.execute(q, args)]


def _check(conn, body: dict) -> tuple[dict, list[dict]]:
    period, code, basis = body.get('period') or '', body.get('item_code') or '', body.get('basis') or ''
    team_id = body.get('scope_team_id') or None
    try:
        amount = float(body.get('amount'))
    except (TypeError, ValueError):
        raise HTTPException(422, '请填写费用池金额')
    if amount <= 0:
        raise HTTPException(422, '费用池金额必须大于 0')
    if basis not in BASES:
        raise HTTPException(422, '分摊标准只能选队天、进尺或元/台月')
    if basis == ANNUAL:
        period = period[:4]
        if not period.isdigit() or len(period) != 4:
            raise HTTPException(422, '请选择归属年度')
    elif len(period) != 7 or period[4] != '-':
        raise HTTPException(422, '请选择归属期间')
    item = next((i for i in allocable_items(conn) if i['item_code'] == code), None)
    if not item:
        raise HTTPException(422, '该科目不开放分摊')
    if team_id and not conn.execute('SELECT 1 FROM team WHERE team_id=?', (team_id,)).fetchone():
        raise HTTPException(422, '队伍不存在')
    rec = recipients(conn, period, basis, team_id)
    if not rec:
        what = '施工时段（钻机台月）' if basis == ANNUAL else f'已审核的“{BASES[basis][0]}”事实'
        raise HTTPException(409, f'{period} 没有{what}，无可分摊的井')
    # 同科目、范围重叠（任一为全部队伍或同一队伍）、期间重叠（年度池覆盖当年各月）的有效池不能并存，否则重复计入
    for o in conn.execute("SELECT pool_id, period, scope_team_id FROM cost_pool WHERE item_code=? AND status='有效'", (code,)):
        same_time = o['period'].startswith(period) or period.startswith(o['period'])
        same_scope = not o['scope_team_id'] or not team_id or o['scope_team_id'] == team_id
        if same_time and same_scope:
            raise HTTPException(409, f'该科目在 {o["period"]} 已有范围重叠的有效费用池 #{o["pool_id"]}，请先作废再重新录入')
    total_w = sum(r['w'] for r in rec)   # 台月标准的分母：全年实际钻机台月（含已有历史导入的月份）
    keep, skipped = [], []
    for r in rec:
        hit = conn.execute("SELECT 1 FROM economic_event WHERE well_id=? AND period=? AND item_code=? AND origin_type='历史导入' "
                           "AND status='有效'", (r['well_id'], r['period'], code)).fetchone()
        if hit and basis != ANNUAL:
            raise HTTPException(409, f"{r['well_id']} {r['period']} 该科目已有历史导入金额，不能再分摊（会重复计入）")
        (skipped if hit else keep).append(r)
    if not keep:
        raise HTTPException(409, f'{period} 各井月该科目都已有历史导入金额，无需再分摊')
    return {'period': period, 'item': item, 'basis': basis, 'team_id': team_id, 'amount': amount, 'note': body.get('note'),
            'total_w': total_w, 'skipped': skipped}, keep


def shares(amount: float, rec: list[dict], total: float | None = None) -> list[dict]:
    """按标准量比例分摊。total 大于本次分摊对象合计时（台月池跳过了已有历史导入的井月），不做尾差调整。"""
    own = sum(r['w'] for r in rec)
    total = total or own
    tail = abs(total - own) < 1e-9
    out, used = [], 0
    for i, r in enumerate(rec):
        a = money(amount) - used if tail and i == len(rec) - 1 else money(amount * r['w'] / total)   # 尾差归最后一口井
        used += a
        out.append({**r, 'amount': a, 'ratio': r['w'] / total})
    return out


def preview(conn, body: dict) -> dict:
    p, rec = _check(conn, body)
    sh = shares(p['amount'], rec, p['total_w'])
    return {'total': money(p['amount']), 'allocated': sum(x['amount'] for x in sh), 'basis_total': round(p['total_w'], 4),
            'unit': BASES[p['basis']][1], 'rate': round(p['amount'] / p['total_w'], 2), 'period': p['period'], 'shares': sh,
            'skipped': p['skipped']}


def create(conn, body: dict, user: dict) -> dict:
    p, rec = _check(conn, body)
    sh = shares(p['amount'], rec, p['total_w'])
    code = p['item']['item_code']
    pool_id = conn.execute(
        "INSERT INTO cost_pool (period, item_code, scope_team_id, basis, amount, note, status, created_by, created_at) "
        "VALUES (?,?,?,?,?,?, '有效', ?, ?)",
        (p['period'], code, p['team_id'], p['basis'], money(p['amount']), p['note'], user['who'], now())).lastrowid
    batch_id = conn.execute('INSERT INTO calc_batch (trigger, scope, engine_version, started_at, operator) VALUES (?,?,?,?,?)',
                            ('费用分摊', jdump({'pool': pool_id, 'periods': sorted({s['period'] for s in sh}), 'wells': sorted({s['well_id'] for s in sh})}),
                             engine.ENGINE_VERSION, now(), user['who'])).lastrowid
    it = conn.execute('SELECT * FROM cost_item WHERE item_code=?', (code,)).fetchone()
    total_w = p['total_w']
    unit = BASES[p['basis']][1]
    rate = p['amount'] / total_w
    for s in sh:
        if p['basis'] == ANNUAL:
            formula = (f"标准 {rate:,.2f} 元/台月（费用池 {money(p['amount']):,} 元 ÷ 全年实际 {total_w:.2f} 台月）"
                       f" × 本井 {s['period']} {s['w']:.2f} 台月")
        else:
            formula = f"费用池 {money(p['amount']):,} 元 × 本井{p['basis']} {engine.fmt_qty(s['w'])} {unit} / 池内合计 {engine.fmt_qty(total_w)} {unit}"
        conn.execute(
            'INSERT INTO economic_event (scenario, event_date, period, well_id, team_id, item_code, direction, cost_behavior, owner_dept, '
            'origin_type, quantity, unit_price, factor, amount, calc_snapshot, batch_id, status, pool_id) '
            "VALUES ('实际',?,?,?,?,?,?,?,?, '分摊', ?,?,1,?,?,?, '有效', ?)",
            (engine.period_end(s['period']), s['period'], s['well_id'], s['team_id'], code, it['direction'], it['cost_behavior'],
             it['owner_dept'], s['w'], rate, s['amount'],
             jdump({'formula': formula, 'pool_id': pool_id, 'basis': p['basis'], 'conditions': []}), batch_id, pool_id))
    conn.execute('UPDATE calc_batch SET finished_at=?, event_count=?, amount_change=? WHERE batch_id=?',
                 (now(), len(sh), sum(x['amount'] for x in sh), batch_id))
    log(conn, '费用池', pool_id, '分摊', user['who'], f"{p['period']} {code} {money(p['amount']):,} 元 按{p['basis']}分到 {len(sh)} 个队井")
    wells = sorted({s['well_id'] for s in sh})
    batch = engine.recalc(conn, wells, sorted({s['period'] for s in sh}), '费用分摊', user['who'])   # 该科目已分摊的井，规则不再重复计算
    return {'pool_id': pool_id, 'shares': sh, 'batch': batch}


def void(conn, pool_id: int, user: dict) -> dict:
    pool = conn.execute('SELECT * FROM cost_pool WHERE pool_id=?', (pool_id,)).fetchone()
    if not pool:
        raise HTTPException(404, '费用池不存在')
    if pool['status'] != '有效':
        raise HTTPException(409, '该费用池已作废')
    batch_id = conn.execute('INSERT INTO calc_batch (trigger, scope, engine_version, started_at, operator) VALUES (?,?,?,?,?)',
                            ('分摊作废', jdump({'pool': pool_id, 'periods': [pool['period']]}), engine.ENGINE_VERSION, now(),
                             user['who'])).lastrowid
    evs = conn.execute("SELECT * FROM economic_event WHERE pool_id=? AND status='有效'", (pool_id,)).fetchall()
    for ev in evs:
        engine.reverse_event(conn, ev, batch_id)
        conn.execute('UPDATE economic_event SET pool_id=? WHERE reversal_of=?', (pool_id, ev['event_id']))
    conn.execute("UPDATE cost_pool SET status='已作废', voided_by=?, voided_at=? WHERE pool_id=?", (user['who'], now(), pool_id))
    conn.execute('UPDATE calc_batch SET finished_at=?, event_count=?, amount_change=? WHERE batch_id=?',
                 (now(), len(evs), -sum(e['amount'] for e in evs), batch_id))
    log(conn, '费用池', pool_id, '作废', user['who'], f'冲销 {len(evs)} 笔分摊事项')
    wells = sorted({e['well_id'] for e in evs})
    periods = sorted({e['period'] for e in evs})
    batch = engine.recalc(conn, wells, periods, '分摊作废', user['who']) if wells else None   # 恢复规则计算
    return {'pool_id': pool_id, 'reversed': len(evs), 'batch': batch}


def list_pools(conn, period: str | None = None) -> list[dict]:
    q = ('SELECT p.*, i.item_name, t.team_no, '
         "(SELECT COUNT(*) FROM economic_event e WHERE e.pool_id=p.pool_id AND e.status='有效') n "
         'FROM cost_pool p JOIN cost_item i ON i.item_code=p.item_code LEFT JOIN team t ON t.team_id=p.scope_team_id ')
    args: tuple = ()
    if period:
        q += 'WHERE p.period=? '
        args = (period,)
    return [dict(r) for r in conn.execute(q + 'ORDER BY p.pool_id DESC LIMIT 200', args)]


def detail(conn, pool_id: int) -> dict:
    pool = conn.execute('SELECT p.*, i.item_name FROM cost_pool p JOIN cost_item i ON i.item_code=p.item_code WHERE pool_id=?',
                        (pool_id,)).fetchone()
    if not pool:
        raise HTTPException(404, '费用池不存在')
    evs = [dict(r) for r in conn.execute(
        "SELECT e.period, e.well_id, t.team_no, e.quantity, e.amount, e.unit_price FROM economic_event e LEFT JOIN team t ON t.team_id=e.team_id "
        "WHERE e.pool_id=? AND e.status='有效' ORDER BY e.period, e.well_id, t.team_no", (pool_id,))]
    rate = evs[0]['unit_price'] if evs else None
    return {**dict(pool), 'unit': BASES[pool['basis']][1], 'events': evs, 'total': money(sum(e['amount'] for e in evs)),
            'rate': round(rate, 2) if rate is not None else None}
