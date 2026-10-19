"""经营结果：全部从经济事项汇总，不另存金额。

口径：总成本 = 固定成本 + 变动成本（变动成本含安全计提、科研计提、税金）；
毛益额 = 形象收入 − 变动成本；净利润 = 形象收入 − 总成本。
"""
import calendar
import re
from datetime import date

from fastapi import HTTPException

from . import auth, engine, wellinfo
from .db import jdump, jload, log, money, now

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
    total = {}
    for it in reversed(items):
        c = it['item_code']
        total[c] = money(amt.get(c, 0)) if it['is_leaf'] else sum(total[k] for k in children.get(c, []))
    w = conn.execute('SELECT * FROM well WHERE well_id=?', (well_id,)).fetchone()
    rev = total.get('R02', 0)
    fixed, vari = total.get('C1', 0), total.get('C2', 0)
    tree = [{
        'code': it['item_code'], 'name': it['item_name'], 'level': it['level'], 'is_leaf': bool(it['is_leaf']),
        'parent': it['parent_code'], 'dept': it['owner_dept'], 'calc_type': it['calc_type'], 'col': it['report_col'],
        'behavior': it['cost_behavior'], 'amount': total[it['item_code']], 'origins': origins.get(it['item_code'], []),
    } for it in items if it['direction'] == '成本']
    return {
        'tree': tree, 'by': by, 'total': total,
        'kpi': {'revenue': rev, 'contract': money(w['contract_amount']), 'fixed': fixed, 'variable': vari,
                'total_cost': fixed + vari, 'gross_margin': rev - vari, 'net_profit': rev - fixed - vari},
        'show_revenue': True,
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


SUM_KEYS = ('revenue', 'fixed', 'variable', 'total_cost', 'gross_margin', 'net_profit')
# 决策层运营指标：进尺、钻机台月、工况小时；非生产时间 = 待命 + 复杂情况
OPS_KEYS = ('footage', 'cost_with_footage', 'rig_months', 'net_with_rig', 'cost_with_rig', 'hours_total', 'npt_hours')
NPT_CONDITIONS = ('待命', '复杂情况')


def depth_footage(conn, well_id: str, month: str) -> float:
    """进尺口径：取实际井深。
    有已审核日报的井：进尺 = 日报井深差（结束井深 − 起始井深）逐日累计到月，各月合计即实际井深。
    没有日报的井（模板导入的历史井）：进尺 = 单井基础信息里的实际井深，整口井计入它最后一个历史导入月份，避免多月重复计入。"""
    has_facts = conn.execute("SELECT 1 FROM business_fact WHERE well_id=? AND fact_type='进尺' AND status='已审核' LIMIT 1",
                             (well_id,)).fetchone()
    if has_facts:
        return conn.execute("SELECT COALESCE(SUM(quantity), 0) q FROM business_fact WHERE well_id=? AND fact_type='进尺' "
                            "AND status='已审核' AND substr(fact_date,1,7)=?", (well_id, month)).fetchone()['q']
    last = conn.execute("SELECT MAX(period) p FROM economic_event WHERE well_id=? AND origin_type='历史导入' AND status='有效'",
                        (well_id,)).fetchone()['p']
    if last != month:
        return 0.0
    return conn.execute('SELECT actual_depth FROM well WHERE well_id=?', (well_id,)).fetchone()['actual_depth'] or 0.0


def _ops(conn, well_id: str, month: str) -> dict:
    """某井某月的进尺（取实际井深，见 depth_footage）、钻机台月（施工时段天数 ÷ 当月天数，口径同费用导出）与工况小时。"""
    footage = depth_footage(conn, well_id, month)
    y, mo = int(month[:4]), int(month[5:])
    days_in = calendar.monthrange(y, mo)[1]
    first, last = date(y, mo, 1), date(y, mo, days_in)
    days = 0
    for sg in conn.execute('SELECT start_at, end_at FROM team_well_period WHERE well_id=?', (well_id,)):
        a = max(date.fromisoformat(sg['start_at'][:10]), first)
        b = min(date.fromisoformat(sg['end_at'][:10]) if sg['end_at'] else last, last)
        if a <= b:
            days += (b - a).days + 1
    hours: dict[str, float] = {}
    for f in conn.execute("SELECT attributes, quantity FROM business_fact WHERE well_id=? AND fact_type='工况时间' AND status='已审核' "
                          'AND substr(fact_date,1,7)=?', (well_id, month)):
        k = jload(f['attributes']).get('work_condition') or '其他'
        hours[k] = hours.get(k, 0) + f['quantity']
    return {'footage': footage, 'rig_months': min(days, days_in) / days_in, 'hours': hours}


RIG_CLASSES = ('50 型', '70 型', '80 型以上')


def rig_class(rig_type: str | None) -> str | None:
    """钻机按型号数字分档：≤ 50（含 30、40 型）为 50 型，51–79 为 70 型，≥ 80 为 80 型以上；型号里没有数字的不分类。"""
    m = re.search(r'(\d{2,3})', rig_type or '')
    if not m:
        return None
    n = int(m.group(1))
    return RIG_CLASSES[0] if n <= 50 else RIG_CLASSES[1] if n < 80 else RIG_CLASSES[2]


def _months(conn, period: str) -> list[str]:
    """YYYY-MM 为单月；YYYY 为该年有数据的全部月份（升序）。"""
    if len(period) == 4 and period.isdigit():
        return sorted(p for p in periods(conn) if p.startswith(period + '-'))
    return [period]


def _prev_period(conn, period: str) -> str | None:
    """环比对象：单月取上一个有数据的月份，全年取上一年。"""
    if len(period) == 4:
        prev = str(int(period) - 1)
        return prev if _months(conn, prev) else None
    earlier = [p for p in periods(conn) if p < period]
    return earlier[0] if earlier else None


def _sum_wells(rows: list[dict]) -> dict:
    out = {k: sum(r[k] for r in rows) for k in SUM_KEYS + OPS_KEYS}
    by: dict[str, float] = {}
    for r in rows:
        for k, v in r['hours'].items():
            by[k] = by.get(k, 0) + v
    out['hours'] = by
    out['well_count'] = len(rows)
    out['loss_wells'] = sum(1 for r in rows if r['net_profit'] < 0)
    out['loss_amount'] = sum(r['net_profit'] for r in rows if r['net_profit'] < 0)
    return out


def overview(conn, user: dict, period: str, with_extra: bool = True) -> dict:
    """经营总览：period 为 YYYY-MM（单月）或 YYYY（全年，按月累加）。
    返回各井指标、合计、上期合计（环比）、成本科目结构（二级科目）。历史数据井（未经基层队登记）按完成井处理。"""
    months = _months(conn, period)
    wells = auth.visible_wells(conn, user)
    acc: dict[str, dict] = {}
    structure: dict[str, dict] = {}
    for wid in wells:
        for m in months:
            if not conn.execute('SELECT 1 FROM economic_event WHERE well_id=? AND period=? LIMIT 1', (wid, m)).fetchone():
                continue
            c = compute(conn, user, wid, m)
            k = c['kpi']
            row = acc.get(wid)
            if row is None:
                info = well_info(conn, wid, m)
                row = acc[wid] = {'well_id': wid, 'market': info['market'], 'block': info['block'], 'well_type': info['well_type'],
                                  'team': info['team'], 'phase': info['phase'], 'status': info['status'], 'client': info['client'] or '',
                                  'rig_type': info['rig_type'], 'rig_class': rig_class(info['rig_type']),
                                  'remark': remark(conn, wid, m), 'last_period': m, **{x: 0 for x in SUM_KEYS + OPS_KEYS},
                                  'hours': {}}
            else:
                info = well_info(conn, wid, m)   # 全年累计时以最近一个月的队伍、开次、状态、钻机为准
                row.update(team=info['team'], phase=info['phase'], status=info['status'], remark=remark(conn, wid, m), last_period=m,
                           rig_type=info['rig_type'], rig_class=rig_class(info['rig_type']))
            for x in ('revenue', 'fixed', 'variable', 'total_cost', 'net_profit'):
                row[x] += k[x]
            row['gross_margin'] += k['gross_margin']
            ops = _ops(conn, wid, m)
            row['footage'] += ops['footage']
            row['cost_with_footage'] += k['total_cost'] if ops['footage'] else 0   # 每米成本只算有进尺的井·月
            row['rig_months'] += ops['rig_months']
            row['net_with_rig'] += k['net_profit'] if ops['rig_months'] else 0      # 每台月利润只算有施工时段的井·月
            row['cost_with_rig'] += k['total_cost'] if ops['rig_months'] else 0
            for hk, hv in ops['hours'].items():
                row['hours'][hk] = row['hours'].get(hk, 0) + hv
                row['hours_total'] += hv
                if hk in NPT_CONDITIONS:
                    row['npt_hours'] += hv
            if with_extra:
                for n in c['tree']:
                    if n['level'] == 2 and n['amount']:
                        d = structure.setdefault(n['code'], {'code': n['code'], 'name': n['name'], 'behavior': n['behavior'], 'amount': 0})
                        d['amount'] += n['amount']
    rows = sorted(acc.values(), key=lambda r: r['well_id'])
    for r in rows:
        if r['remark'] == '历史数据井':
            r['status'] = '完井'   # 历史数据井默认为完成井
    out = {'period': period, 'months': months, 'wells': rows, 'sum': _sum_wells(rows),
           'show_revenue': True, 'scope': user['scope_label']}
    if with_extra:
        prev = _prev_period(conn, period)
        out['prev'] = {'period': prev, 'sum': overview(conn, user, prev, False)['sum']} if prev else None
        out['structure'] = sorted(structure.values(), key=lambda d: -d['amount'])
    return out


def trend(conn, user: dict) -> list[dict]:
    """各月合计（收入、成本、净利润、井数），供经营总览趋势图。"""
    out = []
    for p in sorted(periods(conn)):
        o = overview(conn, user, p, False)
        out.append({'period': p, 'well_count': len(o['wells']), **o['sum']})
    return out


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
    """未经基层钻井队登记基础信息的井（模板导入建档）备注“历史数据井”；已登记的井，本期金额含历史导入时备注“历史数据导入”。"""
    if not conn.execute("SELECT 1 FROM well_info_report WHERE well_id=? AND status IN ('已提交', '已被更新') LIMIT 1", (well_id,)).fetchone():
        return '历史数据井'
    hit = conn.execute("SELECT 1 FROM economic_event WHERE well_id=? AND period=? AND origin_type='历史导入' AND status='有效' LIMIT 1",
                       (well_id, period)).fetchone()
    return '历史数据导入' if hit else ''


def _check_item(conn, user: dict, well_id: str, code: str) -> dict:
    auth.check_well(conn, user, well_id)
    it = conn.execute('SELECT * FROM cost_item WHERE item_code=?', (code,)).fetchone()
    if not it or not it['is_leaf']:
        raise HTTPException(404, '费用项目不存在或不在数据范围内')
    return dict(it)


def _src_label(conn, ev) -> str:
    if ev['origin_type'] == '规则计算' and ev['fact_id']:
        f = conn.execute('SELECT f.*, d.version FROM business_fact f LEFT JOIN daily_report d ON d.report_id=f.report_id '
                         'WHERE f.fact_id=?', (ev['fact_id'],)).fetchone()
        return f"成本日报 {f['fact_date'][5:]} · v{f['version'] or 1}"
    if ev['origin_type'] == '分摊':
        return f"费用分摊 · 费用池 #{ev['pool_id']}"
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
        unit = e['fact_unit'] or ('元' if e['origin_type'] == '比例计提' else
                                  {'队天': '队天', '进尺': '米'}.get(jload(e['calc_snapshot']).get('basis'), '') if e['origin_type'] == '分摊' else '')
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
    elif e['origin_type'] == '分摊':
        out['formula'] = f"{snap.get('formula', '')} = {e['amount']:,.0f} 元"
        out['rule_lines'] = [{'k': '口径', 'v': f"费用分摊 · 按{snap.get('basis', '')}"}, {'k': '归口', 'v': it['owner_dept'] or '—'},
                             {'k': '计算顺序', 'v': '月末财务取数 → 费用池 → 队 → 井'}]
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
    elif e['origin_type'] == '分摊':
        pool = conn.execute('SELECT * FROM cost_pool WHERE pool_id=?', (e['pool_id'],)).fetchone()
        out['src_title'] = '费用池'
        out['src_lines'] = [{'k': '费用池', 'v': f"#{e['pool_id']} · {pool['period']} · {pool['amount']:,.0f} 元" if pool else '—'},
                            {'k': '分摊标准', 'v': pool['basis'] if pool else '—'},
                            {'k': '录入', 'v': f"{pool['created_by']} · {pool['created_at'][:16]}" if pool else '—'},
                            {'k': '批次', 'v': f"费用分摊 · 批次 {e['batch_id']}"}]
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

DIMS = {'market': '市场', 'block': '区块', 'team': '队伍', 'well': '井'}
BM_KEYS = ('revenue', 'fixed', 'variable', 'total_cost', 'gross_margin', 'net_profit', 'footage', 'cost_with_footage')


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
         **{k: sum(r[k] for r in rows) for k in BM_KEYS}}
    return _ratios(g)


# 排名指标与方向：True 为越高越好。合计类（进尺、收入、毛益额、净利润）按分组合计排名，率类按分组合计后再求率；
# 口径同 compute：毛益额 = 形象收入 − 变动成本，毛益率 = 毛益额 ÷ 形象收入，净利润 = 形象收入 − 总成本（固定 + 变动）。
RANK_METRICS = {'net_rate': True, 'footage': True, 'revenue': True, 'gross_margin': True, 'gm_rate': True, 'net_profit': True,
                'avg_net': True, 'avg_revenue': True, 'avg_cost': False, 'cost_per_m': False}


def ranking(groups: list[dict], metric: str) -> list[int]:
    """按指标排名，返回分组下标；没有该指标值（如没有收入时的率、没有进尺时的单位进尺成本）的分组不参与排名。"""
    high = RANK_METRICS[metric]
    idx = [i for i, g in enumerate(groups) if g[metric] is not None]
    return sorted(idx, key=lambda i: -groups[i][metric] if high else groups[i][metric])


def benchmark(conn, period: str | list[str], dims: list[str], filters: dict[str, list[str]] | None = None) -> dict:
    """经营对标。
    period：可多选的期间，元素为 YYYY-MM（单月）、YYYY（整年）或 all；多个期间取并集，空或 all 表示全部期间。
    filters：每个维度选定的具体对象（如 队伍 = 70101、70103），只对比这些对象；不选表示该维度全部。
    dims：分组维度，市场 / 区块 / 队伍 / 井；前端按“单选维度 + 下钻”使用，接口仍支持组合。
    返回各分组、所选范围合计（scope）与全部井合计（total），基准由前端在三者或某一分组（标杆）中选择。
    options 为各维度可选对象，按其他维度已选条件联动收窄。单井指标按“井·月”平均；单位进尺成本只用有进尺的井·月计算（进尺取实际井深，见 depth_footage）。"""
    dims = [d for d in dims if d in DIMS] or ['market']
    filters = {d: [v for v in (filters or {}).get(d, []) if v] for d in DIMS}
    periods_all = [r['period'] for r in conn.execute('SELECT DISTINCT period FROM economic_event ORDER BY period')]
    sel = [period] if isinstance(period, str) else [x for x in period if x]
    if not sel or 'all' in sel:
        periods = periods_all
    else:
        periods = [p for p in periods_all if any(p == x or (len(x) == 4 and p.startswith(x + '-')) for x in sel)]
    period_options = [{'value': p, 'wells': conn.execute(
        'SELECT COUNT(DISTINCT well_id) n FROM economic_event WHERE period=? AND well_id NOT IN (SELECT well_id FROM well_deleted)',
        (p,)).fetchone()['n']} for p in periods_all]
    rows = []
    for p in periods:
        for r in conn.execute('SELECT DISTINCT well_id FROM economic_event WHERE period=? '
                              'AND well_id NOT IN (SELECT well_id FROM well_deleted) ORDER BY well_id', (p,)):
            wid = r['well_id']
            info = well_info(conn, wid, p)
            k = compute(conn, None, wid, p)['kpi']
            footage = depth_footage(conn, wid, p)
            rows.append({'well_id': wid, 'well': wid, 'period': p, 'market': info['market'], 'block': info['block'], 'team': info['team'],
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
    scope, total = _aggregate(scoped, {}), _aggregate(rows, {})
    # 分组个数：合计类指标以“全部井 / 所选范围”为基准时，前端取每组平均值作比较
    scope['group_count'], total['group_count'] = len(groups), len({tuple(r[d] for d in dims) for r in rows})
    return {'period': 'all' if not sel else ','.join(sel), 'selected': periods, 'periods': periods_all, 'period_options': period_options, 'dims': dims, 'dim_labels': DIMS, 'filters': filters, 'options': options,
            'filtered': any(filters.values()), 'groups': groups, 'scope': scope, 'total': total,
            'rank': {m: ranking(groups, m) for m in RANK_METRICS}}


def delete_well(conn, well_id: str, user: dict) -> dict:
    """手工删除：经济事项只冲不改，所以冲销该井全部有效事项并登记删除，井不再出现在经营总览、对标、导出和填报中。"""
    if not conn.execute('SELECT 1 FROM well WHERE well_id=?', (well_id,)).fetchone():
        raise HTTPException(404, '井号不存在')
    if conn.execute('SELECT 1 FROM well_deleted WHERE well_id=?', (well_id,)).fetchone():
        raise HTTPException(409, '该井已删除')
    batch = conn.execute(
        "INSERT INTO calc_batch (trigger, scope, engine_version, started_at, operator) VALUES ('手工删除井', ?, ?, ?, ?)",
        (jdump({'wells': [well_id]}), engine.ENGINE_VERSION, now(), user['who'])).lastrowid
    n = 0
    for ev in conn.execute("SELECT e.*, NULL rule_code FROM economic_event e WHERE well_id=? AND status='有效'", (well_id,)).fetchall():
        engine.reverse_event(conn, ev, batch)
        n += 1
    conn.execute('UPDATE calc_batch SET finished_at=?, event_count=? WHERE batch_id=?', (now(), n, batch))
    conn.execute('INSERT INTO well_deleted (well_id, deleted_at, deleted_by) VALUES (?,?,?)', (well_id, now(), user['who']))
    log(conn, '井', well_id, '删除', user['who'], f'冲销 {n} 笔经济事项')
    return {'ok': True, 'reversed': n}
