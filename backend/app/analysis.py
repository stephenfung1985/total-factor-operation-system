"""经营结果：全部从经济事项汇总，不另存金额。

口径：总成本 = 固定成本 + 变动成本（变动成本含安全计提、科研计提、税金）；
毛益额 = 形象收入 − 变动成本；净利润 = 形象收入 − 总成本。
"""
import re

from fastapi import HTTPException

from . import auth, engine, wellinfo
from .db import jload, money

def periods(conn) -> list[str]:
    return [r['period'] for r in conn.execute('SELECT DISTINCT period FROM economic_event ORDER BY period DESC')]


def _items(conn) -> list[dict]:
    return [dict(r) for r in conn.execute('SELECT * FROM cost_item ORDER BY sort_no')]


def _well_segment(conn, well_id: str, period: str):
    end = engine.period_end(period)
    start = period + '-01'
    return conn.execute(
        'SELECT p.*, t.team_no, t.name team_name, t.rig_type, t.captain FROM team_well_period p JOIN team t ON t.team_id=p.team_id '
        'WHERE p.well_id=? AND p.start_at<=? AND (p.end_at IS NULL OR p.end_at>=?) ORDER BY p.start_at DESC LIMIT 1',
        (well_id, end, start)).fetchone() or conn.execute(
        'SELECT p.*, t.team_no, t.name team_name, t.rig_type, t.captain FROM team_well_period p JOIN team t ON t.team_id=p.team_id '
        'WHERE p.well_id=? ORDER BY p.start_at DESC LIMIT 1', (well_id,)).fetchone()


def compute(conn, user: dict | None, well_id: str, period: str) -> dict:
    """按权限过滤后的成本树金额与经营指标。user 为 None 表示不过滤（导出）。"""
    items = _items(conn)
    amt = {r['item_code']: r['a'] for r in conn.execute(
        'SELECT item_code, SUM(amount) a FROM economic_event WHERE well_id=? AND period=? GROUP BY item_code', (well_id, period))}
    origins = {}
    for r in conn.execute("SELECT DISTINCT item_code, origin_type FROM economic_event WHERE well_id=? AND period=? AND status='有效'",
                          (well_id, period)):
        origins.setdefault(r['item_code'], []).append(r['origin_type'])
    children = {}
    for it in items:
        if it['parent_code']:
            children.setdefault(it['parent_code'], []).append(it['item_code'])
    by = {it['item_code']: it for it in items}
    visible, total = {}, {}
    for it in reversed(items):
        c = it['item_code']
        if it['is_leaf']:
            visible[c] = user is None or auth.item_visible(user, it)
            total[c] = money(amt.get(c, 0)) if visible[c] else 0
        else:
            kids = [k for k in children.get(c, []) if visible.get(k)]
            visible[c] = bool(kids)
            total[c] = sum(total[k] for k in kids)
    show_rev = user is None or user['role'] != 'dept'
    w = conn.execute('SELECT * FROM well WHERE well_id=?', (well_id,)).fetchone()
    rev = total.get('R02', 0) if show_rev else 0
    fixed, vari = total.get('C1', 0), total.get('C2', 0)
    tree = [{
        'code': it['item_code'], 'name': it['item_name'], 'level': it['level'], 'is_leaf': bool(it['is_leaf']),
        'parent': it['parent_code'], 'dept': it['owner_dept'], 'calc_type': it['calc_type'], 'col': it['report_col'],
        'behavior': it['cost_behavior'], 'amount': total[it['item_code']], 'origins': origins.get(it['item_code'], []),
    } for it in items if visible[it['item_code']] and it['direction'] == '成本']
    return {
        'tree': tree, 'by': by, 'total': total,
        'kpi': {'revenue': rev, 'contract': (money(w['contract_amount']) if show_rev else 0), 'fixed': fixed, 'variable': vari,
                'total_cost': fixed + vari, 'gross_margin': rev - vari, 'net_profit': rev - fixed - vari},
        'show_revenue': show_rev,
    }


def well_info(conn, well_id: str, period: str) -> dict:
    w = dict(conn.execute('SELECT * FROM well WHERE well_id=?', (well_id,)).fetchone())
    seg = _well_segment(conn, well_id, period)
    w.update({'team': seg['team_name'] if seg else '—', 'team_id': seg['team_id'] if seg else None,
              'team_no': seg['team_no'] if seg else '—',
              'phase': seg['phase'] if seg else '—', 'rig_type': seg['rig_type'] if seg else '—',
              'captain': seg['captain'] if seg else '—'})
    info = wellinfo.latest(conn, well_id)   # 基层队填报的单井基础信息优先
    if info:
        w.update({'rig_type': info['rig_type'] or w['rig_type'], 'captain': info['captain'] or w['captain']})
    return w


def overview(conn, user: dict, period: str) -> dict:
    wells = auth.visible_wells(conn, user)
    rows = []
    for wid in wells:
        has = conn.execute('SELECT 1 FROM economic_event WHERE well_id=? AND period=? LIMIT 1', (wid, period)).fetchone()
        if not has:
            continue
        info = well_info(conn, wid, period)
        k = compute(conn, user, wid, period)['kpi']
        rows.append({'well_id': wid, 'block': info['block'], 'well_type': info['well_type'], 'team': info['team'],
                     'phase': info['phase'], 'status': info['status'], 'remark': remark(conn, wid, period), **k})
    keys = ('revenue', 'fixed', 'variable', 'total_cost', 'gross_margin', 'net_profit')
    sums = {k: sum(r[k] for r in rows) for k in keys}
    return {'period': period, 'wells': rows, 'sum': sums, 'show_revenue': user['role'] != 'dept', 'scope': user['scope_label']}


def well_result(conn, user: dict, well_id: str, period: str) -> dict:
    auth.check_well(conn, user, well_id)
    r = compute(conn, user, well_id, period)
    info = well_info(conn, well_id, period)
    info['remark'] = remark(conn, well_id, period)
    info['import_sources'] = [x['source_note'] for x in conn.execute(
        "SELECT DISTINCT source_note FROM economic_event WHERE well_id=? AND period=? AND origin_type='历史导入' AND status='有效'",
        (well_id, period))]
    return {'info': info, 'kpi': r['kpi'], 'tree': r['tree'], 'show_revenue': r['show_revenue'],
            'can_export': user['role'] in ('finance', 'team'), 'period': period}


def remark(conn, well_id: str, period: str) -> str:
    """本期金额含历史导入（模板导入或期初导入）时备注“历史数据导入”。"""
    hit = conn.execute("SELECT 1 FROM economic_event WHERE well_id=? AND period=? AND origin_type='历史导入' AND status='有效' LIMIT 1",
                       (well_id, period)).fetchone()
    return '历史数据导入' if hit else ''


def _check_item(conn, user: dict, well_id: str, code: str) -> dict:
    auth.check_well(conn, user, well_id)
    it = conn.execute('SELECT * FROM cost_item WHERE item_code=?', (code,)).fetchone()
    if not it or not it['is_leaf'] or not auth.item_visible(user, dict(it)):
        raise HTTPException(404, '费用项目不存在或不在数据范围内')
    return dict(it)


def _src_label(conn, ev) -> str:
    if ev['origin_type'] == '规则计算' and ev['fact_id']:
        f = conn.execute('SELECT f.*, d.version FROM business_fact f LEFT JOIN daily_report d ON d.report_id=f.report_id '
                         'WHERE f.fact_id=?', (ev['fact_id'],)).fetchone()
        return f"成本日报 {f['fact_date'][5:]} · v{f['version'] or 1}"
    if ev['origin_type'] == '比例计提':
        snap = jload(ev['calc_snapshot'])
        return f"比例计提 · 基数 {snap.get('base_item', '')} 形象收入"
    return ev['source_note'] or ev['origin_type']


def item_events(conn, user: dict, well_id: str, code: str, period: str) -> dict:
    it = _check_item(conn, user, well_id, code)
    evs = conn.execute(
        'SELECT e.*, r.rule_code, r.version rule_version, f.unit fact_unit FROM economic_event e '
        'LEFT JOIN economic_rule r ON r.rule_id=e.rule_id LEFT JOIN business_fact f ON f.fact_id=e.fact_id '
        'WHERE e.well_id=? AND e.item_code=? AND e.period=? ORDER BY e.event_date, e.event_id', (well_id, code, period)).fetchall()
    rows = []
    for e in evs:
        unit = e['fact_unit'] or ('元' if e['origin_type'] == '比例计提' else '')
        if e['origin_type'] == '比例计提':
            qty, price = f"{e['quantity']:,.0f} 元", f"{e['unit_price'] * 100:.2f}%"
        elif e['quantity'] is not None:
            qty, price = f"{engine.fmt_qty(e['quantity'])} {unit}", f"{e['unit_price']:,.4f}"
        else:
            qty, price = '—', '—'
        rows.append({
            'event_id': e['event_id'], 'date': e['event_date'], 'qty': qty, 'price': price,
            'factor': f"{e['factor']:.2f}" if e['factor'] is not None else '—', 'amount': e['amount'],
            'rule': f"{e['rule_code']} · v{e['rule_version']}" if e['rule_code'] else '待规则回算',
            'source': _src_label(conn, e), 'origin': e['origin_type'], 'status': e['status'], 'reversal_of': e['reversal_of'],
        })
    return {'item': {'code': it['item_code'], 'name': it['item_name'], 'behavior': it['cost_behavior'], 'dept': it['owner_dept'],
                     'calc_type': it['calc_type'], 'col': it['report_col']},
            'events': rows, 'total': money(sum(r['amount'] for r in rows))}


def trace(conn, user: dict, event_id: int) -> dict:
    e = conn.execute('SELECT * FROM economic_event WHERE event_id=?', (event_id,)).fetchone()
    if not e:
        raise HTTPException(404, '事项不存在')
    it = _check_item(conn, user, e['well_id'], e['item_code'])
    snap = jload(e['calc_snapshot'])
    name = re.sub(r'^(（\d+）|\d+\.)', '', it['item_name'])
    out = {'event_id': event_id, 'title': f"{e['well_id']} · {name} · {e['event_date']}", 'amount': e['amount'],
           'origin': e['origin_type'], 'status': e['status'], 'reversal_of': e['reversal_of'], 'clause': None,
           'rule_lines': [], 'src_title': '来源', 'src_lines': [], 'conditions': snap.get('conditions', [])}
    if e['reversal_of']:
        out['note'] = f"本笔为冲销记录，冲销事项 #{e['reversal_of']}；冲销由事实更正或规则新版本触发"
    elif e['status'] == '已冲销':
        rev = conn.execute('SELECT event_id, batch_id FROM economic_event WHERE reversal_of=?', (event_id,)).fetchone()
        out['note'] = f"本笔已被冲销（冲销记录 #{rev['event_id']}，计算批次 {rev['batch_id']}）" if rev else '本笔已被冲销'
    if e['rule_id']:
        r = conn.execute('SELECT * FROM economic_rule WHERE rule_id=?', (e['rule_id'],)).fetchone()
        out['formula'] = f"{snap.get('formula', '')} = {e['amount']:,.0f} 元"
        out['rule_lines'] = [
            {'k': '规则', 'v': f"{r['rule_code']} · v{r['version']} · {r['status']}"},
            {'k': '计价方式', 'v': f"{it['calc_type'] or ''} · {r['pricing_method']}"},
            {'k': '适用条件', 'v': '；'.join(snap.get('conditions') or []) or '无（全部适用）'},
            {'k': '生效日期', 'v': r['valid_from']},
        ]
        if snap.get('factor_notes'):
            out['rule_lines'].append({'k': '系数', 'v': '；'.join(snap['factor_notes'])})
        if e['origin_type'] == '比例计提':
            out['rule_lines'][1] = {'k': '计价方式', 'v': 'A 比例计提'}
            out['rule_lines'].insert(2, {'k': '基数', 'v': 'R02 形象收入'})
            out['rule_lines'].append({'k': '计算顺序', 'v': '收入 → 直接成本 → 比例计提'})
        if r['clause_id']:
            c = conn.execute('SELECT c.*, d.doc_name FROM clause c JOIN source_doc d ON d.doc_id=c.doc_id WHERE clause_id=?',
                             (r['clause_id'],)).fetchone()
            out['clause'] = {'ref': f"{c['doc_name']} {c['clause_no']}" + (f" · {c['page_no']}" if c['page_no'] else ''),
                             'pre': c['text_pre'], 'hit': c['text_hit'], 'post': c['text_post']}
    else:
        out['formula'] = '历史导入金额，尚未由规则计算'
        out['rule_lines'] = [{'k': '状态', 'v': '待规则回算'}, {'k': '计价类型', 'v': it['calc_type'] or '—'},
                             {'k': '归口', 'v': it['owner_dept'] or '—'}]
    if e['origin_type'] == '规则计算' and e['fact_id']:
        f = conn.execute('SELECT f.*, d.version, d.reporter, d.reviewer, d.status rstatus, t.name team_name FROM business_fact f '
                         'LEFT JOIN daily_report d ON d.report_id=f.report_id JOIN team t ON t.team_id=f.team_id WHERE f.fact_id=?',
                         (e['fact_id'],)).fetchone()
        out['src_title'] = '来源事实'
        out['src_lines'] = [
            {'k': '成本日报', 'v': f"{e['well_id']} · {f['fact_date']} · 版本 {f['version'] or 1}"},
            {'k': '填报', 'v': f"{f['team_name']} · {f['reporter'] or '—'}"},
            {'k': '审核', 'v': f"{f['reviewer'] or '—'} · {f['rstatus'] or ''}"},
            {'k': f['fact_type'], 'v': f"{engine.fmt_qty(f['quantity'])} {f['unit']}"},
            {'k': '事实状态', 'v': f['status']},
        ]
    elif e['origin_type'] == '比例计提':
        out['src_title'] = '基数事项'
        out['src_lines'] = [{'k': '基数', 'v': f"形象收入 · {e['period']}"}, {'k': '金额', 'v': f"{e['quantity']:,.0f} 元"},
                            {'k': '联动', 'v': '收入变动时本笔一并冲销重算'}]
    else:
        out['src_lines'] = [{'k': '来源', 'v': e['source_note'] or '—'}, {'k': '总表列', 'v': it['report_col'] or '—'},
                            {'k': '批次', 'v': f"历史导入 · 批次 {e['batch_id']}"}]
    return out


def export_rows(conn, well_id: str, period: str) -> list[dict]:
    """行序与《单井经营情况导出模板》一致：表头 18 行 + 成本树 + 经营数据。"""
    info = well_info(conn, well_id, period)
    r = compute(conn, None, well_id, period)
    k, total = r['kpi'], r['total']
    y, m = period.split('-')
    pct = lambda a, b: f'{a / b * 100:.1f}%' if b else '—'  # noqa: E731
    rows = [('井号', well_id), ('年份', y), ('月份', m), ('市场分布', info['market']), ('队号', info['team_no']),
            ('队长', info['captain']), ('钻机类型', info['rig_type']), ('甲方单位', info['client']), ('区块', info['block']),
            ('井别', info['well_category']), ('井型', info['well_type']), ('开次', info['phase']),
            ('设计井深（米）', info['design_depth']), ('实际井深（米）', info['actual_depth']),
            ('合同收入（元）', money(info['contract_amount'])), ('形象收入（元）', k['revenue']),
            ('形象利润（元）', k['net_profit']), ('总成本（元）', k['total_cost'])]
    out = [{'k': a, 'v': b, 'kind': 'bold' if a == '总成本（元）' else 'plain', 'level': 1} for a, b in rows]
    for n in r['tree']:
        out.append({'k': n['name'], 'v': total[n['code']], 'kind': 'bold' if n['level'] <= 2 and not n['is_leaf'] else 'plain',
                    'level': n['level'], 'code': n['code']})
    out.append({'k': '经营数据', 'v': None, 'kind': 'section', 'level': 1})
    for a, b, kind in [('总收入（元）', k['revenue'], 'plain'), ('总成本（元）', k['total_cost'], 'plain'),
                       ('（一）固定成本（元）', k['fixed'], 'plain'), ('（二）变动成本（元）', k['variable'], 'plain'),
                       ('毛益额（元）', k['gross_margin'], 'bold'), ('毛益率（%）', pct(k['gross_margin'], k['revenue']), 'plain'),
                       ('净利润（元）', k['net_profit'], 'bold'), ('利润率（%）', pct(k['net_profit'], k['revenue']), 'plain')]:
        out.append({'k': a, 'v': b, 'kind': kind, 'level': 1})
    return out


# ---------- 经营对标 ----------

DIMS = {'market': '市场', 'block': '区块', 'team': '队伍'}
SUM_KEYS = ('revenue', 'fixed', 'variable', 'total_cost', 'gross_margin', 'net_profit', 'footage', 'cost_with_footage')


def _ratios(g: dict) -> dict:
    n = g['well_months'] or 1
    g.update({
        'gm_rate': g['gross_margin'] / g['revenue'] if g['revenue'] else None,
        'net_rate': g['net_profit'] / g['revenue'] if g['revenue'] else None,
        'avg_cost': g['total_cost'] / n, 'avg_net': g['net_profit'] / n, 'avg_revenue': g['revenue'] / n,
        'cost_per_m': g['cost_with_footage'] / g['footage'] if g['footage'] else None,
    })
    return g


def _aggregate(rows: list[dict], keys: dict) -> dict:
    g = {'keys': keys, 'well_count': len({r['well_id'] for r in rows}), 'well_months': len(rows),
         'items': [{k: r[k] for k in ('well_id', 'period', 'market', 'block', 'team', 'revenue', 'total_cost', 'gross_margin',
                                      'net_profit', 'footage', 'remark')} for r in rows],
         **{k: sum(r[k] for r in rows) for k in SUM_KEYS}}
    return _ratios(g)


def benchmark(conn, period: str, dims: list[str], filters: dict[str, list[str]] | None = None) -> dict:
    """经营对标。
    filters：每个维度选定的具体对象（如 队伍 = 70101、70103），只对比这些对象；不选表示该维度全部。
    dims：分组方式，市场 / 区块 / 队伍任意组合。
    返回各分组、所选范围合计（scope）与全部井合计（total），基准由前端在三者或某一分组（标杆）中选择。
    options 为各维度可选对象，按其他维度已选条件联动收窄。单井指标按“井·月”平均；单位进尺成本只用有进尺事实的井·月计算。"""
    dims = [d for d in dims if d in DIMS] or ['market']
    filters = {d: [v for v in (filters or {}).get(d, []) if v] for d in DIMS}
    periods = periods_all = [r['period'] for r in conn.execute('SELECT DISTINCT period FROM economic_event ORDER BY period')]
    if period != 'all':
        periods = [period] if period in periods_all else []
    rows = []
    for p in periods:
        for r in conn.execute('SELECT DISTINCT well_id FROM economic_event WHERE period=? ORDER BY well_id', (p,)):
            wid = r['well_id']
            info = well_info(conn, wid, p)
            k = compute(conn, None, wid, p)['kpi']
            footage = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) q FROM business_fact WHERE well_id=? AND fact_type='进尺' AND status='已审核' "
                'AND substr(fact_date,1,7)=?', (wid, p)).fetchone()['q']
            rows.append({'well_id': wid, 'period': p, 'market': info['market'], 'block': info['block'], 'team': info['team'],
                         'remark': remark(conn, wid, p), 'footage': footage,
                         'cost_with_footage': k['total_cost'] if footage else 0, **k})

    def keep(row, skip=None):
        return all(not vals or row[d] in vals for d, vals in filters.items() if d != skip)

    options = {}
    for d in DIMS:
        counts: dict[str, set] = {}
        for row in rows:
            if keep(row, skip=d):
                counts.setdefault(row[d], set()).add(row['well_id'])
        options[d] = [{'value': v, 'wells': len(w)} for v, w in sorted(counts.items())]
    scoped = [r for r in rows if keep(r)]
    buckets: dict[tuple, list] = {}
    for row in scoped:
        buckets.setdefault(tuple(row[d] for d in dims), []).append(row)
    groups = [_aggregate(rs, dict(zip(dims, key))) for key, rs in buckets.items()]
    return {'period': period, 'periods': periods_all, 'dims': dims, 'dim_labels': DIMS, 'filters': filters, 'options': options,
            'filtered': any(filters.values()), 'groups': groups, 'scope': _aggregate(scoped, {}), 'total': _aggregate(rows, {})}
