"""费用分摊：上年发生总额 → 归属年份各队、各井。

全部成本科目都可按需选择填报（下拉选科目）。企划财务部填写：
  归属年份   分摊到哪一年，默认当年；“上年”指归属年份的前一年；
  上年发生总额   该科目上年度实际发生的总金额；
  上年实际钻机台月   上年度全部（或所选市场）实际钻机台月；
  范围   全部 / 大港 / 长庆 / 西北（按井所属市场）。
分摊标准 = 上年该科目发生总额 ÷ 上年实际钻机台月（元/台月）；归属年份一律沿用其上一年数据计算，不再逐期录入。
归属年份各 (队伍, 井, 月) 分得 标准 × 当月钻机台月，写入“分摊”类经济事项；
钻机台月 = 施工时段落在当月的天数 ÷ 当月天数（与费用导出口径一致），当年只算到今天为止。
作废时冲销该池全部事项；分摊后该井该月该科目不再由规则重复计算（见 engine._desired）。
历史上按队天 / 进尺录入的月度池只保留查看与作废。
"""
import calendar
from datetime import date

from fastapi import HTTPException

from . import engine
from .db import jdump, log, money, now

BASES = {'队天': ('日历天', '队天'), '进尺': ('进尺', '米'), '台月': ('钻机台月', '台月')}   # 仅用于显示历史池
BASIS = '台月'
SCOPES = ['大港', '长庆', '西北']   # 范围：空 = 全部


def allocable_items(conn) -> list[dict]:
    return [{'item_code': r['item_code'], 'item_name': r['item_name'], 'owner_dept': r['owner_dept']}
            for r in conn.execute("SELECT item_code, item_name, owner_dept FROM cost_item WHERE is_leaf=1 AND direction='成本' "
                                  'ORDER BY sort_no')]


def years(conn) -> list[str]:
    """可选的归属年份：当年及有施工时段的年份，最新在前。"""
    this = date.today().year
    ys = {this} | {int(r['y']) for r in conn.execute('SELECT DISTINCT substr(start_at,1,4) y FROM team_well_period') if (r['y'] or '').isdigit()}
    return [str(y) for y in sorted((y for y in ys if y <= this), reverse=True)]


def options(conn) -> dict:
    ys = years(conn)
    return {'items': allocable_items(conn), 'scopes': SCOPES, 'years': ys, 'year': ys[0],
            'ref_rig_months': {y: ref_rig_months(conn, str(int(y) - 1)) for y in ys}}


def ref_rig_months(conn, year: str) -> dict:
    """上年实际钻机台月参考值：按施工时段自动算出，键为范围（'' = 全部）。仅供填报参考，以财务填写为准。"""
    rows = conn.execute('SELECT w.market, p.team_id, p.well_id FROM team_well_period p JOIN well w ON w.well_id=p.well_id').fetchall()
    market = {r['well_id']: r['market'] for r in rows}
    out = {k: 0.0 for k in ['', *SCOPES]}
    for r in rig_months(conn, year, None):
        out[''] += r['w']
        if market.get(r['well_id']) in out:
            out[market[r['well_id']]] += r['w']
    return {k: round(v, 2) for k, v in out.items()}


def rig_months(conn, year: str, market: str | None, today: date | None = None) -> list[dict]:
    """全年实际钻机台月：按 (队伍, 井, 月) 汇总施工时段天数 ÷ 当月天数，只算到今天。"""
    today = today or date.today()
    y = int(year)
    stop = min(date(y, 12, 31), today)
    out: dict[tuple, float] = {}
    q = ('SELECT p.team_id, p.well_id, p.start_at, p.end_at FROM team_well_period p JOIN well w ON w.well_id=p.well_id '
         'WHERE p.well_id NOT IN (SELECT well_id FROM well_deleted) ')
    args: list = []
    if market:
        q += 'AND w.market=? '
        args.append(market)
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


def _check(conn, body: dict) -> tuple[dict, list[dict]]:
    year = str(body.get('year') or date.today().year)
    if year not in years(conn):
        raise HTTPException(422, '请选择归属年份')
    code = body.get('item_code') or ''
    market = body.get('scope_market') or None
    if market and market not in SCOPES:
        raise HTTPException(422, '范围只能选全部、大港、长庆或西北')
    try:
        amount = float(body.get('amount'))
    except (TypeError, ValueError):
        raise HTTPException(422, '请填写上年发生总额')
    if amount <= 0:
        raise HTTPException(422, '上年发生总额必须大于 0')
    try:
        prev_rm = float(body.get('prev_rig_months'))
    except (TypeError, ValueError):
        raise HTTPException(422, '请填写上年实际钻机台月')
    if prev_rm <= 0:
        raise HTTPException(422, '上年实际钻机台月必须大于 0')
    item = next((i for i in allocable_items(conn) if i['item_code'] == code), None)
    if not item:
        raise HTTPException(422, '请选择成本科目')
    rec = rig_months(conn, year, market)
    if not rec:
        raise HTTPException(409, f'{year} 年{market or ""}没有施工时段（钻机台月），无可分摊的井')
    # 同科目、范围重叠（任一为全部或同一市场）、同一归属年份已有的有效池不能并存，否则重复计入
    for o in conn.execute("SELECT pool_id, period, scope_market, scope_team_id FROM cost_pool WHERE item_code=? AND status='有效'", (code,)):
        if (o['period'].startswith(year) or year.startswith(o['period'])) and (
                not o['scope_market'] or not market or o['scope_market'] == market or o['scope_team_id']):
            raise HTTPException(409, f'该科目在 {o["period"]} 已有范围重叠的有效费用池 #{o["pool_id"]}，请先作废再重新录入')
    keep, skipped = [], []
    for r in rec:
        hit = conn.execute("SELECT 1 FROM economic_event WHERE well_id=? AND period=? AND item_code=? AND origin_type='历史导入' "
                           "AND status='有效'", (r['well_id'], r['period'], code)).fetchone()
        (skipped if hit else keep).append(r)
    if not keep:
        raise HTTPException(409, f'{year} 年各井月该科目都已有历史导入金额，无需再分摊')
    return {'period': year, 'item': item, 'basis': BASIS, 'market': market, 'amount': amount, 'prev_rig_months': prev_rm,
            'note': body.get('note'), 'skipped': skipped}, keep


def shares(rate: float, rec: list[dict]) -> list[dict]:
    """各 (队伍, 井, 月) 分得 标准 × 当月钻机台月。"""
    return [{**r, 'amount': money(rate * r['w'])} for r in rec]


def preview(conn, body: dict) -> dict:
    p, rec = _check(conn, body)
    rate = p['amount'] / p['prev_rig_months']
    sh = shares(rate, rec)
    return {'total': money(p['amount']), 'allocated': sum(x['amount'] for x in sh), 'prev_rig_months': p['prev_rig_months'],
            'unit': BASES[BASIS][1], 'rate': round(rate, 2), 'period': p['period'], 'shares': sh, 'skipped': p['skipped']}


def create(conn, body: dict, user: dict) -> dict:
    p, rec = _check(conn, body)
    rate = p['amount'] / p['prev_rig_months']
    sh = shares(rate, rec)
    code = p['item']['item_code']
    pool_id = conn.execute(
        "INSERT INTO cost_pool (period, item_code, scope_market, basis, amount, prev_rig_months, note, status, created_by, created_at) "
        "VALUES (?,?,?,?,?,?,?, '有效', ?, ?)",
        (p['period'], code, p['market'], BASIS, money(p['amount']), p['prev_rig_months'], p['note'], user['who'], now())).lastrowid
    batch_id = conn.execute('INSERT INTO calc_batch (trigger, scope, engine_version, started_at, operator) VALUES (?,?,?,?,?)',
                            ('费用分摊', jdump({'pool': pool_id, 'periods': sorted({s['period'] for s in sh}), 'wells': sorted({s['well_id'] for s in sh})}),
                             engine.ENGINE_VERSION, now(), user['who'])).lastrowid
    it = conn.execute('SELECT * FROM cost_item WHERE item_code=?', (code,)).fetchone()
    for s in sh:
        formula = (f"标准 {rate:,.2f} 元/台月（上年发生总额 {money(p['amount']):,} 元 ÷ 上年实际 {p['prev_rig_months']:.2f} 台月）"
                   f" × 本井 {s['period']} {s['w']:.2f} 台月")
        conn.execute(
            'INSERT INTO economic_event (scenario, event_date, period, well_id, team_id, item_code, direction, cost_behavior, owner_dept, '
            'origin_type, quantity, unit_price, factor, amount, calc_snapshot, batch_id, status, pool_id) '
            "VALUES ('实际',?,?,?,?,?,?,?,?, '分摊', ?,?,1,?,?,?, '有效', ?)",
            (engine.period_end(s['period']), s['period'], s['well_id'], s['team_id'], code, it['direction'], it['cost_behavior'],
             it['owner_dept'], s['w'], rate, s['amount'],
             jdump({'formula': formula, 'pool_id': pool_id, 'basis': BASIS, 'conditions': []}), batch_id, pool_id))
    conn.execute('UPDATE calc_batch SET finished_at=?, event_count=?, amount_change=? WHERE batch_id=?',
                 (now(), len(sh), sum(x['amount'] for x in sh), batch_id))
    log(conn, '费用池', pool_id, '分摊', user['who'], f"{p['period']} {code} 上年 {money(p['amount']):,} 元 ÷ {p['prev_rig_months']:.2f} 台月 分到 {len(sh)} 个队井")
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
    pool = conn.execute('SELECT p.*, i.item_name, t.team_no FROM cost_pool p JOIN cost_item i ON i.item_code=p.item_code '
                        'LEFT JOIN team t ON t.team_id=p.scope_team_id WHERE pool_id=?',
                        (pool_id,)).fetchone()
    if not pool:
        raise HTTPException(404, '费用池不存在')
    evs = [dict(r) for r in conn.execute(
        "SELECT e.period, e.well_id, t.team_no, e.quantity, e.amount, e.unit_price FROM economic_event e LEFT JOIN team t ON t.team_id=e.team_id "
        "WHERE e.pool_id=? AND e.status='有效' ORDER BY e.period, e.well_id, t.team_no", (pool_id,))]
    rate = evs[0]['unit_price'] if evs else None
    return {**dict(pool), 'unit': BASES[pool['basis']][1], 'events': evs, 'total': money(sum(e['amount'] for e in evs)),
            'rate': round(rate, 2) if rate is not None else None}
