"""单井经营结果模板导入：按《单井经营情况导出模板》列转行，以“历史导入”写入经济事项。

一个工作表一口井一个月：A 列项目、B 列金额，行序与模板一致。先预览、后确认。
- 金额按末级项目导入，汇总行由系统重算；汇总行与明细不一致时提示。
- 同一井同一月已有历史导入的，金额不同则冲销旧记录后重新导入（只冲不改），相同记为无变化。
- 该井该月已有规则计算的事项时不允许导入，避免与系统计算重复。
- 新井按表头建档、按队号挂施工时段；已有井以单井基础信息为准，表头不一致只提示。
- 导入的井在经营总览中备注“历史数据导入”。
"""
import re
from io import BytesIO

import openpyxl

from . import engine
from .db import log, money, now
from .excel import TEMPLATE, whole_yuan_format
from .wellinfo import SECTIONS

HEAD_ROWS = 18
SUMMARY_LABELS = ['经营数据', '总收入（元）', '总成本（元）', '（一）固定成本（元）', '（二）变动成本（元）', '毛益额（元）', '毛益率（%）',
                  '净利润（元）', '利润率（%）']


class ImportError_(Exception):
    pass


def template_labels() -> list[str]:
    ws = openpyxl.load_workbook(TEMPLATE).active
    return [str(ws.cell(r, 1).value or '').strip() for r in range(1, ws.max_row + 1)]


def blank_template() -> bytes:
    wb = openpyxl.load_workbook(TEMPLATE)
    ws = wb.active
    whole_yuan_format(ws)
    for r in range(1, ws.max_row + 1):
        ws.cell(r, 2).value = None
    ws.title = '井号-年月'
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _num(v):
    if v is None or v == '':
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '').replace('，', '')
    try:
        return float(s)
    except ValueError:
        return None


def _s(v) -> str:
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def parse(data: bytes) -> list[dict]:
    try:
        wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
    except Exception as e:  # noqa: BLE001
        raise ImportError_(f'无法读取文件：{e}') from e
    sheets = []
    for ws in wb.worksheets:
        cells = [(str(ws.cell(r, 1).value or '').strip(), ws.cell(r, 2).value) for r in range(1, ws.max_row + 1)]
        if not cells or cells[0][0] != '井号':
            continue
        sheets.append({'sheet': ws.title, 'cells': cells})
    if not sheets:
        raise ImportError_('没有找到《单井经营情况导出模板》格式的工作表（A1 应为“井号”）')
    return sheets


def _cost_items(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM cost_item WHERE direction='成本' ORDER BY sort_no")]


def check(conn, sheet: dict, labels: list[str], items: list[dict], seen: set) -> dict:
    cells = sheet['cells']
    blocks, warns = [], []
    if len(cells) < len(labels):
        blocks.append(f'行数 {len(cells)} 少于模板的 {len(labels)} 行')
    for i, lab in enumerate(labels):
        if i < len(cells) and cells[i][0] != lab:
            blocks.append(f'第 {i + 1} 行项目“{cells[i][0]}”与模板“{lab}”不一致')
            if len(blocks) > 5:
                break
    head = {k: v for k, v in cells[:HEAD_ROWS]}
    wid = _s(head.get('井号'))
    y, m = _s(head.get('年份')), _s(head.get('月份'))
    period = f'{int(y):04d}-{int(m):02d}' if y.isdigit() and m.isdigit() and 1 <= int(m) <= 12 else ''
    if not wid:
        blocks.append('井号为空')
    if not period:
        blocks.append('年份、月份不正确')
    if (wid, period) in seen:
        blocks.append('文件内同一井同一月重复')
    seen.add((wid, period))

    amounts, bad = {}, []
    for n, it in enumerate(items):
        row = HEAD_ROWS + n
        if row >= len(cells):
            break
        v = _num(cells[row][1])
        if v is None:
            bad.append(f'第 {row + 1} 行')
            v = 0.0
        amounts[it['item_code']] = v
    if bad:
        blocks.append('金额不是数字：' + '、'.join(bad[:5]) + (' 等' if len(bad) > 5 else ''))
    revenue = _num(head.get('形象收入（元）'))
    if revenue is None:
        blocks.append('形象收入不是数字')
        revenue = 0.0

    # 汇总行与明细比对：按末级明细导入，汇总由系统重算
    children: dict[str, list[str]] = {}
    for it in items:
        if it['parent_code']:
            children.setdefault(it['parent_code'], []).append(it['item_code'])
    calc: dict[str, float] = {}
    for it in reversed(items):
        c = it['item_code']
        calc[c] = amounts.get(c, 0.0) if it['is_leaf'] else sum(calc[k] for k in children.get(c, []))
    for it in items:
        if not it['is_leaf'] and abs(calc[it['item_code']] - amounts.get(it['item_code'], 0.0)) > 1:
            warns.append(f'{it["item_name"]} 合计 {amounts.get(it["item_code"], 0):,.2f} 与明细合计 {calc[it["item_code"]]:,.2f} 不一致，按明细导入')
    for rr in conn.execute("SELECT DISTINCT r.item_code, i.item_name, r.unit_price FROM economic_rule r JOIN cost_item i "
                           "ON i.item_code=r.item_code WHERE r.status='已发布' AND r.calc_order=3"):
        if revenue and money(amounts.get(rr['item_code'], 0.0)) == 0:
            warns.append(f'{rr["item_name"]} 为 0，导入后按已发布规则计提（形象收入 × {rr["unit_price"] * 100:g}%）')
    total = calc.get('C1', 0) + calc.get('C2', 0)
    tc = _num(head.get('总成本（元）'))
    if tc is not None and abs(tc - total) > 1:
        warns.append(f'总成本 {tc:,.2f} 与明细合计 {total:,.2f} 不一致，按明细导入')

    team_no = _s(head.get('队号'))
    team = None
    if team_no:
        team = conn.execute('SELECT * FROM team WHERE team_no=? OR name=?', (team_no, team_no)).fetchone()
    well = conn.execute('SELECT * FROM well WHERE well_id=?', (wid,)).fetchone() if wid else None
    if well:
        diffs = [lab for lab, col in (('市场分布', 'market'), ('区块', 'block'), ('井别', 'well_category'), ('井型', 'well_type'))
                 if _s(head.get(lab)) and _s(head.get(lab)) != (well[col] or '')]
        if diffs:
            warns.append(f'{"、".join(diffs)}与系统中的单井信息不一致，以系统为准')
    elif wid and not team_no:
        blocks.append('新井必须填写队号')
    if wid and period:
        engine_n = conn.execute(
            "SELECT COUNT(*) n FROM economic_event WHERE well_id=? AND period=? AND status='有效' AND origin_type IN ('规则计算','比例计提') "
            "AND item_code IN (SELECT item_code FROM cost_item WHERE direction='成本' OR item_code='R02')", (wid, period)).fetchone()['n']
        imported_n = conn.execute("SELECT COUNT(*) n FROM economic_event WHERE well_id=? AND period=? AND origin_type='历史导入' "
                                  "AND status='有效'", (wid, period)).fetchone()['n']
        if engine_n and not imported_n:
            blocks.append(f'{wid} {period} 已有规则计算的经济事项，历史数据只能导入系统上线前的期间')

    new = {c: money(v) for c, v in amounts.items() if _leaf(items, c) and money(v) != 0}   # 入库按整数元
    if money(revenue) != 0:
        new['R02'] = money(revenue)
    cost_in = sum(v for c, v in new.items() if c != 'R02')   # 预览合计 = 入库后各末级科目之和
    old = {}
    if wid and period:
        for r in conn.execute("SELECT item_code, SUM(amount) a FROM economic_event WHERE well_id=? AND period=? AND origin_type='历史导入' "
                              "AND status='有效' GROUP BY item_code", (wid, period)):
            old[r['item_code']] = money(r['a'])
    if blocks:
        action = '阻断'
    elif old and old == new:
        action = '无变化'
    elif old:
        action = '覆盖'
    else:
        action = '导入'
    return {'sheet': sheet['sheet'], 'well_id': wid, 'period': period, 'team_no': team_no, 'new_well': bool(wid) and not well,
            'new_team': bool(team_no) and not team, 'revenue': new.get('R02', 0), 'total_cost': cost_in,
            'net_profit': new.get('R02', 0) - cost_in, 'action': action, 'blocks': blocks, 'warns': warns,
            '_head': head, '_new': new, '_team': dict(team) if team else None}


def _leaf(items, code):
    return any(it['item_code'] == code and it['is_leaf'] for it in items)


def run(conn, data: bytes, filename: str, user: dict, commit: bool) -> dict:
    labels = template_labels()
    items = _cost_items(conn)
    if len(items) + HEAD_ROWS + len(SUMMARY_LABELS) != len(labels):
        raise ImportError_('系统成本项目树与导出模板行数不一致，请先同步模板')
    seen: set = set()
    results = [check(conn, s, labels, items, seen) for s in parse(data)]
    if commit:
        batch_id = conn.execute(
            "INSERT INTO calc_batch (trigger, scope, engine_version, started_at, operator) VALUES ('模板导入', ?, 'import-1.0', ?, ?)",
            (filename, now(), user['who'])).lastrowid
        n = 0
        for r in results:
            if r['action'] not in ('导入', '覆盖'):
                continue
            _write(conn, r, filename, batch_id, user)
            n += len(r['_new'])
        conn.execute('UPDATE calc_batch SET finished_at=?, event_count=? WHERE batch_id=?', (now(), n, batch_id))
    count: dict[str, int] = {}
    for r in results:
        count[r['action']] = count.get(r['action'], 0) + 1
    return {'count': count, 'committed': commit,
            'sheets': [{k: v for k, v in r.items() if not k.startswith('_')} for r in results]}


def _write(conn, r: dict, filename: str, batch_id: int, user: dict) -> None:
    head, wid, period = r['_head'], r['well_id'], r['period']
    team = r['_team']
    if not team and not r['team_no']:
        tid = None   # 已有井、表中未填队号：沿用系统中的施工时段
    elif not team:
        tid = 'T' + re.sub(r'\s+', '', r['team_no'])
        conn.execute('INSERT INTO team (team_id, team_no, name, rig_type, unit, captain) VALUES (?,?,?,?, NULL, ?)',
                     (tid, r['team_no'], f"{r['team_no']} 钻井队", _s(head.get('钻机类型')) or None, _s(head.get('队长')) or None))
    else:
        tid = team['team_id']
    phase = _s(head.get('开次'))
    if phase.isdigit() and 1 <= int(phase) <= 5:
        phase = SECTIONS[int(phase) - 1]
    depth = _num(head.get('实际井深（米）')) or 0
    contract = money(_num(head.get('合同收入（元）')) or 0)
    if r['new_well']:
        conn.execute("INSERT INTO well VALUES (?,?,?,?,?,?,?,?,?,?, '正钻')",
                     (wid, _s(head.get('市场分布')) or '—', _s(head.get('区块')) or '—', _s(head.get('井别')) or '—',
                      _s(head.get('井型')) or '—', SECTIONS.index(phase) + 1 if phase in SECTIONS else 1,
                      _num(head.get('设计井深（米）')) or 0, depth, _s(head.get('甲方单位')) or None, contract))
    else:
        conn.execute('UPDATE well SET actual_depth=MAX(actual_depth, ?), contract_amount=CASE WHEN contract_amount=0 THEN ? '
                     'ELSE contract_amount END WHERE well_id=?', (depth, contract, wid))
    start, end = period + '-01', engine.period_end(period)
    if tid and not conn.execute('SELECT 1 FROM team_well_period WHERE well_id=? AND start_at<=? AND (end_at IS NULL OR end_at>=?)',
                        (wid, end, start)).fetchone():
        conn.execute('INSERT INTO team_well_period (team_id, well_id, phase, start_at, end_at) VALUES (?,?,?,?,?)',
                     (tid, wid, phase or '—', start, end))
    if r['action'] == '覆盖':
        for ev in conn.execute("SELECT e.*, NULL rule_code FROM economic_event e WHERE well_id=? AND period=? AND origin_type='历史导入' "
                               "AND status='有效'", (wid, period)).fetchall():
            engine.reverse_event(conn, ev, batch_id)
    item = {x['item_code']: x for x in conn.execute('SELECT * FROM cost_item')}
    note = f"单井经营情况模板导入 · {filename} · {r['sheet']}"
    for code, amt in r['_new'].items():
        it = item[code]
        conn.execute(
            "INSERT INTO economic_event (event_date, period, well_id, team_id, item_code, direction, cost_behavior, owner_dept, "
            "origin_type, amount, batch_id, status, source_note) VALUES (?,?,?,?,?,?,?,?, '历史导入', ?,?, '有效', ?)",
            (end, period, wid, tid, code, it['direction'], it['cost_behavior'], it['owner_dept'], amt, batch_id, note))
    log(conn, '经营结果', f'{wid}/{period}', '模板导入' if r['action'] == '导入' else '模板覆盖导入', user['who'], note)
    engine.recalc(conn, [wid], [period], '模板导入', user['who'])
