"""费用分摊：费用池 → 队 → 井。

只对一部分科目开放（折旧、长摊、项目部运行费用、制造费这类月末按财务实际取数的固定成本）：
企划财务部录入某月某科目的费用池金额，选择分摊标准（队天 / 进尺），系统按当月已审核事实里
各 (队伍, 井) 的标准量比例分到井，写入“分摊”类经济事项；尾差归最后一口井。
作废时冲销该池全部事项；分摊后该井该月该科目不再由规则重复计算（见 engine._desired）。
"""
from fastapi import HTTPException

from . import engine
from .db import jdump, log, money, now

ALLOCABLE_PREFIXES = ('C1-02', 'C1-03', 'C1-04', 'C1-05')
BASES = {'队天': ('日历天', '队天'), '进尺': ('进尺', '米')}


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
    return {'items': allocable_items(conn), 'bases': list(BASES), 'periods': periods, 'teams': teams}


def recipients(conn, period: str, basis: str, team_id: str | None) -> list[dict]:
    fact_type = BASES[basis][0]
    q = ("SELECT f.team_id, f.well_id, SUM(f.quantity) w FROM business_fact f WHERE f.status='已审核' AND f.scenario='实际' "
         'AND substr(f.fact_date,1,7)=? AND f.fact_type=? AND f.well_id NOT IN (SELECT well_id FROM well_deleted) ')
    args = [period, fact_type]
    if team_id:
        q += 'AND f.team_id=? '
        args.append(team_id)
    q += 'GROUP BY f.team_id, f.well_id HAVING SUM(f.quantity)>0 ORDER BY f.well_id, f.team_id'
    return [dict(r) for r in conn.execute(q, args)]


def _check(conn, body: dict) -> tuple[dict, list[dict]]:
    period, code, basis = body.get('period') or '', body.get('item_code') or '', body.get('basis') or ''
    team_id = body.get('scope_team_id') or None
    try:
        amount = float(body.get('amount'))
    except (TypeError, ValueError):
        raise HTTPException(422, '请填写费用池金额')
    if amount <= 0:
        raise HTTPException(422, '费用池金额必须大于 0')
    if len(period) != 7 or period[4] != '-':
        raise HTTPException(422, '请选择归属期间')
    item = next((i for i in allocable_items(conn) if i['item_code'] == code), None)
    if not item:
        raise HTTPException(422, '该科目不开放分摊')
    if basis not in BASES:
        raise HTTPException(422, '分摊标准只能选队天或进尺')
    if team_id and not conn.execute('SELECT 1 FROM team WHERE team_id=?', (team_id,)).fetchone():
        raise HTTPException(422, '队伍不存在')
    rec = recipients(conn, period, basis, team_id)
    if not rec:
        raise HTTPException(409, f'{period} 没有已审核的“{BASES[basis][0]}”事实，无可分摊的井')
    dup = conn.execute("SELECT pool_id FROM cost_pool WHERE period=? AND item_code=? AND status='有效' AND scope_team_id IS ?",
                       (period, code, team_id)).fetchone()
    if dup:
        raise HTTPException(409, f'该期间、该科目、该范围已有有效费用池 #{dup["pool_id"]}，请先作废再重新录入')
    for r in rec:
        hit = conn.execute("SELECT 1 FROM economic_event WHERE well_id=? AND period=? AND item_code=? AND origin_type='历史导入' "
                           "AND status='有效'", (r['well_id'], period, code)).fetchone()
        if hit:
            raise HTTPException(409, f"{r['well_id']} {period} 该科目已有历史导入金额，不能再分摊（会重复计入）")
    return {'period': period, 'item': item, 'basis': basis, 'team_id': team_id, 'amount': amount, 'note': body.get('note')}, rec


def shares(amount: float, rec: list[dict]) -> list[dict]:
    total = sum(r['w'] for r in rec)
    out, used = [], 0
    for i, r in enumerate(rec):
        a = money(amount) - used if i == len(rec) - 1 else money(amount * r['w'] / total)   # 尾差归最后一口井
        used += a
        out.append({**r, 'amount': a, 'ratio': r['w'] / total})
    return out


def preview(conn, body: dict) -> dict:
    p, rec = _check(conn, body)
    sh = shares(p['amount'], rec)
    return {'total': money(p['amount']), 'basis_total': sum(r['w'] for r in rec), 'unit': BASES[p['basis']][1], 'shares': sh}


def create(conn, body: dict, user: dict) -> dict:
    p, rec = _check(conn, body)
    sh = shares(p['amount'], rec)
    code = p['item']['item_code']
    pool_id = conn.execute(
        "INSERT INTO cost_pool (period, item_code, scope_team_id, basis, amount, note, status, created_by, created_at) "
        "VALUES (?,?,?,?,?,?, '有效', ?, ?)",
        (p['period'], code, p['team_id'], p['basis'], money(p['amount']), p['note'], user['who'], now())).lastrowid
    batch_id = conn.execute('INSERT INTO calc_batch (trigger, scope, engine_version, started_at, operator) VALUES (?,?,?,?,?)',
                            ('费用分摊', jdump({'pool': pool_id, 'periods': [p['period']], 'wells': sorted({s['well_id'] for s in sh})}),
                             engine.ENGINE_VERSION, now(), user['who'])).lastrowid
    it = conn.execute('SELECT * FROM cost_item WHERE item_code=?', (code,)).fetchone()
    total_w = sum(r['w'] for r in rec)
    unit = BASES[p['basis']][1]
    for s in sh:
        conn.execute(
            'INSERT INTO economic_event (scenario, event_date, period, well_id, team_id, item_code, direction, cost_behavior, owner_dept, '
            'origin_type, quantity, unit_price, factor, amount, calc_snapshot, batch_id, status, pool_id) '
            "VALUES ('实际',?,?,?,?,?,?,?,?, '分摊', ?,?,1,?,?,?, '有效', ?)",
            (engine.period_end(p['period']), p['period'], s['well_id'], s['team_id'], code, it['direction'], it['cost_behavior'],
             it['owner_dept'], s['w'], p['amount'] / total_w, s['amount'],
             jdump({'formula': f"费用池 {money(p['amount']):,} 元 × 本井{p['basis']} {engine.fmt_qty(s['w'])} {unit} / 池内合计 {engine.fmt_qty(total_w)} {unit}",
                    'pool_id': pool_id, 'basis': p['basis'], 'conditions': []}), batch_id, pool_id))
    conn.execute('UPDATE calc_batch SET finished_at=?, event_count=?, amount_change=? WHERE batch_id=?',
                 (now(), len(sh), money(p['amount']), batch_id))
    log(conn, '费用池', pool_id, '分摊', user['who'], f"{p['period']} {code} {money(p['amount']):,} 元 按{p['basis']}分到 {len(sh)} 个队井")
    wells = sorted({s['well_id'] for s in sh})
    batch = engine.recalc(conn, wells, [p['period']], '费用分摊', user['who'])   # 该科目已分摊的井，规则不再重复计算
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
    batch = engine.recalc(conn, wells, [pool['period']], '分摊作废', user['who']) if wells else None   # 恢复规则计算
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
        "SELECT e.well_id, t.team_no, e.quantity, e.amount FROM economic_event e LEFT JOIN team t ON t.team_id=e.team_id "
        "WHERE e.pool_id=? AND e.status='有效' ORDER BY e.well_id, t.team_no", (pool_id,))]
    return {**dict(pool), 'unit': BASES[pool['basis']][1], 'events': evs, 'total': money(sum(e['amount'] for e in evs))}
