"""成本日报：一井一队一天一张，只报量、不含金额。

表体按事实类型分段（见 report_lines.SECTIONS）：进尺与工况 → 人员出勤 → 能耗与材料 → 设备在用 → 外包技术服务 → 运输与环保。
每段只列需要基层采集、参与成本计算的报量项目；默认显示常用项目、本井规则计价的项目和近 30 天报过的项目，
其余可按需添加。没有已发布规则的量也可以报，由归口部室据此补规则。
"""
from datetime import date as Date, timedelta

from . import engine, report_lines
from .db import jdump, jload, log, now

WORK_CONDITIONS = ['钻进', '起下钻', '接单根', '循环', '下套管', '固井', '测井', '待命', '复杂情况', '其他']
HOUR_NAMES = WORK_CONDITIONS
FOOTAGE_WARN = 300          # 单日进尺合理上限（米）
USAGE_WARN = 1.3            # 用量高于同开次历史均值的倍数
LATE_DAYS = 7               # 补报时限（天）
RECENT_DAYS = 30            # 近期报过的项目默认显示
AUTO_TYPES = ('进尺', '日历天')   # 由井深、施工期自动生成的事实
CARRY_SECTIONS = ('staff', 'equip')   # 新日报沿用上一张已审核日报的人员出勤与在用设备
SERVICE_CONDITION = {'C2-3-11': '固井', 'C2-3-14': '测井', 'C2-3-08': '下套管', 'C2-3-04': '钻进'}


class ReportError(Exception):
    pass


def segment(conn, well_id: str, date: str, team_id: str | None = None):
    q = ('SELECT p.*, t.name team_name FROM team_well_period p JOIN team t ON t.team_id=p.team_id '
         'WHERE p.well_id=? AND p.start_at<=? AND (p.end_at IS NULL OR p.end_at>=?)')
    args = [well_id, date, date]
    if team_id:
        q += ' AND p.team_id=?'
        args.append(team_id)
    return conn.execute(q + ' ORDER BY p.start_at DESC LIMIT 1', args).fetchone()


def line_defs(conn, well_id: str, date: str, team_id: str | None) -> list[dict]:
    """报量项目及其计价情况：规则指定报量项目的（如 柴油 → RL-C2-1-04）记为规则计价并默认显示；
    只按事实类型取量的规则（如 伙食费 按全部人员出勤）列为该类项目的参与规则。"""
    ctx = engine.well_context(conn, well_id, date, team_id)
    by_line: dict[str, list[str]] = {}
    by_fact: dict[str, list[str]] = {}
    for r in engine.published_rules(conn, date, (1, 2)):
        if r['fact_type'] in AUTO_TYPES or not r['fact_type']:
            continue
        conds = engine.rule_conditions(conn, r['rule_id'])
        if not all(engine.cond_match(ctx, c['dimension'], c['operator'], c['value']) for c in conds):
            continue
        line = report_lines.rule_line(r)
        target = by_line.setdefault(line, []) if line else by_fact.setdefault(r['fact_type'], [])
        if r['rule_code'] not in target:
            target.append(r['rule_code'])
    since = (Date.fromisoformat(date) - timedelta(days=RECENT_DAYS)).isoformat()
    recent = set()
    for r in conn.execute("SELECT lines FROM daily_report WHERE well_id=? AND report_date>=? AND report_date<? AND status!='已被更正'",
                          (well_id, since, date)):
        recent |= {k for k, v in report_lines.canon_map(jload(r['lines'])).items() if float(v or 0) > 0}
    out = []
    for ln in report_lines.lines(conn):
        k = ln['line_key']
        own = by_line.get(k, [])
        out.append({
            'key': k, 'section': ln['section'], 'group': ln['group_name'], 'label': ln['label'], 'unit': ln['unit'],
            'fact_type': ln['fact_type'], 'item_code': ln['item_code'], 'item_name': ln['item_name'], 'dept': ln['owner_dept'],
            'needs_doc': bool(ln['needs_doc']), 'core': bool(ln['core']),
            'rules': own + [c for c in by_fact.get(ln['fact_type'], []) if c not in own],
            'priced': bool(own) or bool(by_fact.get(ln['fact_type'])), 'recent': k in recent,
            'show': bool(ln['core']) or bool(own) or k in recent,
        })
    return out


def header(conn, well_id: str, date: str, team_id: str | None) -> dict:
    w = conn.execute('SELECT * FROM well WHERE well_id=?', (well_id,)).fetchone()
    if not w:
        raise ReportError('井号不存在')
    seg = segment(conn, well_id, date, team_id)
    return {'well_id': well_id, 'date': date, 'team_id': seg['team_id'] if seg else team_id,
            'team': seg['team_name'] if seg else '—', 'block': w['block'], 'well_type': w['well_type'],
            'phase': seg['phase'] if seg else '—', 'in_period': bool(seg)}


def validate(conn, rep: dict, defs: list[dict] | None = None) -> list[dict]:
    msgs = []
    hours = rep.get('hours') or {}
    total = sum(float(hours.get(h) or 0) for h in WORK_CONDITIONS)
    d0, d1 = float(rep.get('depth_start') or 0), float(rep.get('depth_end') or 0)
    footage = d1 - d0
    if not segment(conn, rep['well_id'], rep['report_date'], rep['team_id']):
        msgs.append(('阻断', '施工期', '当天不在这口井的施工期，不能报这口井的事实'))
    if abs(total - 24) > 1e-6:
        msgs.append(('阻断', '工况合计', f'工况合计 {total:g} 小时，必须等于 24 小时'))
    if any(float(hours.get(h) or 0) < 0 for h in WORK_CONDITIONS):
        msgs.append(('阻断', '工况', '工况小时不能为负'))
    if footage < 0:
        msgs.append(('阻断', '井深', '结束井深小于起始井深'))
    if footage > 0 and float(hours.get('钻进') or 0) <= 0:
        msgs.append(('阻断', '进尺与工况', '当天无钻进工况却有进尺'))

    defs = defs if defs is not None else line_defs(conn, rep['well_id'], rep['report_date'], rep['team_id'])
    by = {d['key']: d for d in defs}
    lines = report_lines.canon_map(rep.get('lines'))
    notes = report_lines.canon_map(rep.get('notes') or rep.get('line_notes'))
    staff = 0.0
    for k, v in lines.items():
        try:
            v = float(v or 0)
        except (TypeError, ValueError):
            msgs.append(('阻断', '数量', f'{k} 数量不是数字'))
            continue
        d = by.get(k)
        if d is None:
            if v:
                msgs.append(('阻断', '报量项目', f'报量项目 {k} 不在报量项目字典中'))
            continue
        if v < 0:
            msgs.append(('阻断', '数量', f'{d["label"]} 数量不能为负'))
        if v <= 0:
            continue
        if d['section'] == 'staff' and d['fact_type'] == '人员出勤':
            staff += v
        if d['needs_doc'] and not str(notes.get(k) or '').strip():
            msgs.append(('阻断', '签证单', f'{d["label"]} 未填签证单号（单据号）'))
        cond = SERVICE_CONDITION.get(k)
        if cond and float(hours.get(cond) or 0) <= 0:
            msgs.append(('警告', '服务与工况', f'报了{d["label"]}，但当天没有“{cond}”工况'))
        if not d['priced']:
            msgs.append(('提示', '未匹配规则', f'{d["label"]} 暂无已发布规则，提交后由{d["dept"] or "归口"}部室补规则计价'))
        hist = [report_lines.canon_map(jload(r['lines'])).get(k) for r in conn.execute(
            "SELECT lines FROM daily_report WHERE well_id=? AND phase=? AND status='已审核' AND report_date<?",
            (rep['well_id'], rep.get('phase'), rep['report_date']))]
        hist = [float(h) for h in hist if h]
        if len(hist) >= 3 and v > USAGE_WARN * (sum(hist) / len(hist)):
            msgs.append(('警告', '用量偏离', f'{d["label"]} {v:g}{d["unit"]} 高于同开次历史水平（均值 {sum(hist) / len(hist):.1f}），审核时重点看'))
    for h, k in ((c, k) for k, c in SERVICE_CONDITION.items() if c != '钻进'):
        if float(hours.get(h) or 0) > 0 and not float(lines.get(k) or 0):
            msgs.append(('警告', '服务与工况', f'有“{h}”工况，但没有报{by[k]["label"] if k in by else h}服务'))
    if total > 0 and staff <= 0:
        msgs.append(('警告', '人员出勤', '当天人员出勤为 0，请核对'))
    if footage > FOOTAGE_WARN:
        msgs.append(('警告', '进尺范围', f'单日进尺 {footage:g} 米，超出合理范围（{FOOTAGE_WARN} 米）'))
    try:
        late = (Date.today() - Date.fromisoformat(rep['report_date'])).days
        if late > LATE_DAYS:
            msgs.append(('警告', '补报时限', f'补报已超过 {LATE_DAYS} 天时限'))
    except ValueError:
        msgs.append(('阻断', '日期', '日期格式不正确'))
    return [{'level': lv, 'rule': r, 'message': m} for lv, r, m in msgs]


def _load(conn, report_id: int) -> dict:
    r = conn.execute('SELECT * FROM daily_report WHERE report_id=?', (report_id,)).fetchone()
    if not r:
        raise ReportError('日报不存在')
    d = dict(r)
    d['hours'] = jload(d['hours'])
    d['lines'] = report_lines.canon_map(jload(d['lines']))
    d['notes'] = report_lines.canon_map(jload(d.pop('line_notes', None)))
    return d


def get_report(conn, report_id: int) -> dict:
    rep = _load(conn, report_id)
    rep['header'] = header(conn, rep['well_id'], rep['report_date'], rep['team_id'])
    rep['line_defs'] = line_defs(conn, rep['well_id'], rep['report_date'], rep['team_id'])
    rep['checks'] = validate(conn, rep, rep['line_defs'])
    rep['history'] = [dict(x) for x in conn.execute(
        "SELECT action, operator, operated_at, comment FROM approval_log WHERE object_type='日报' AND object_id=? ORDER BY id",
        (str(report_id),))]
    return rep


def find_or_new(conn, well_id: str, date: str, team_id: str) -> dict:
    """取当天当前有效的日报；没有则新建：井深接上一张，人员出勤与在用设备沿用上一张已审核日报。"""
    r = conn.execute(
        "SELECT report_id FROM daily_report WHERE well_id=? AND team_id=? AND report_date=? AND status!='已被更正' "
        'ORDER BY version DESC LIMIT 1', (well_id, team_id, date)).fetchone()
    if r:
        return get_report(conn, r['report_id'])
    prev = conn.execute(
        "SELECT * FROM daily_report WHERE well_id=? AND team_id=? AND report_date<? AND status='已审核' "
        'ORDER BY report_date DESC LIMIT 1', (well_id, team_id, date)).fetchone()
    h = header(conn, well_id, date, team_id)
    defs = line_defs(conn, well_id, date, team_id)
    carry = {d['key'] for d in defs if d['section'] in CARRY_SECTIONS}
    lines = {k: v for k, v in report_lines.canon_map(jload(prev['lines'])).items() if k in carry} if prev else {}
    notes = {k: v for k, v in report_lines.canon_map(jload(prev['line_notes'])).items() if k in carry} if prev else {}
    rep = {
        'report_id': None, 'well_id': well_id, 'team_id': team_id, 'report_date': date, 'version': 1, 'status': '新建',
        'phase': h['phase'], 'depth_start': prev['depth_end'] if prev else 0, 'depth_end': prev['depth_end'] if prev else 0,
        'hours': {n: 0 for n in WORK_CONDITIONS}, 'lines': lines, 'notes': notes, 'reporter': None,
        'header': h, 'line_defs': defs, 'history': [], 'carried': bool(lines),
    }
    rep['checks'] = validate(conn, rep, defs)
    return rep


def _fields(body: dict, phase) -> tuple:
    lines = {k: v for k, v in report_lines.canon_map(body.get('lines')).items() if v not in (None, '')}
    notes = {k: str(v).strip() for k, v in report_lines.canon_map(body.get('notes')).items() if str(v or '').strip()}
    return (body.get('depth_start'), body.get('depth_end'), jdump(body.get('hours') or {}), jdump(lines), jdump(notes), phase)


def save_draft(conn, body: dict, user: dict) -> int:
    well_id, date, team_id = body['well_id'], body['report_date'], body['team_id']
    cur = conn.execute(
        "SELECT * FROM daily_report WHERE well_id=? AND team_id=? AND report_date=? AND status!='已被更正' "
        'ORDER BY version DESC LIMIT 1', (well_id, team_id, date)).fetchone()
    seg = segment(conn, well_id, date, team_id)
    fields = _fields(body, seg['phase'] if seg else None)
    if cur and cur['status'] in ('草稿', '驳回'):
        conn.execute("UPDATE daily_report SET depth_start=?, depth_end=?, hours=?, lines=?, line_notes=?, phase=?, status='草稿', "
                     'reporter=? WHERE report_id=?', fields + (user['who'], cur['report_id']))
        return cur['report_id']
    if cur and cur['status'] == '已提交':
        raise ReportError('日报已提交、待审核，不能再改；如需修改请联系审核人驳回')
    if cur and cur['status'] == '已审核':
        raise ReportError('日报已审核，请发起更正')
    rid = conn.execute(
        'INSERT INTO daily_report (well_id, team_id, report_date, status, depth_start, depth_end, hours, lines, line_notes, phase, '
        "reporter) VALUES (?,?,?, '草稿', ?,?,?,?,?,?,?)", (well_id, team_id, date) + fields + (user['who'],)).lastrowid
    log(conn, '日报', rid, '保存草稿', user['who'])
    return rid


def submit(conn, report_id: int, user: dict) -> dict:
    rep = _load(conn, report_id)
    if rep['status'] not in ('草稿', '驳回'):
        raise ReportError(f'当前状态“{rep["status"]}”不能提交')
    checks = validate(conn, rep)
    conn.execute('DELETE FROM validation_alert WHERE report_id=?', (report_id,))
    for c in checks:
        conn.execute('INSERT INTO validation_alert (report_id, check_rule, level, message) VALUES (?,?,?,?)',
                     (report_id, c['rule'], c['level'], c['message']))
    if any(c['level'] == '阻断' for c in checks):
        raise ReportError('存在阻断项，不能提交')
    conn.execute("UPDATE daily_report SET status='已提交', submitted_at=?, reporter=? WHERE report_id=?",
                 (now(), user['who'], report_id))
    facts_from_report(conn, report_id, '已提交')
    log(conn, '日报', report_id, '提交', user['who'])
    return get_report(conn, report_id)


def facts_from_report(conn, report_id: int, status: str) -> None:
    """每个填了数的报量项目生成一条业务事实，粒度为 日期 + 队伍 + 井号；进尺、工况、日历天另行生成。"""
    rep = _load(conn, report_id)
    conn.execute('DELETE FROM business_fact WHERE report_id=? AND status=\'已提交\'', (report_id,))
    base = (report_id, rep['report_date'], rep['team_id'], rep['well_id'])
    ins = ('INSERT INTO business_fact (report_id, fact_date, team_id, well_id, fact_type, quantity, unit, attributes, source, status) '
           'VALUES (?,?,?,?,?,?,?,?,?,?)')
    footage = float(rep['depth_end'] or 0) - float(rep['depth_start'] or 0)
    if footage > 0:
        conn.execute(ins, base + ('进尺', footage, '米', jdump({'depth_start': rep['depth_start'], 'depth_end': rep['depth_end'],
                                                                'phase': rep['phase']}), '基层填报', status))
    conn.execute(ins, base + ('日历天', 1, '队天', jdump({'phase': rep['phase']}), '系统接口', status))
    for h in WORK_CONDITIONS:
        v = float(rep['hours'].get(h) or 0)
        if v > 0:
            conn.execute(ins, base + ('工况时间', v, '小时', jdump({'work_condition': h}), '基层填报', status))
    catalog = {ln['line_key']: ln for ln in report_lines.lines(conn)}
    for k, v in rep['lines'].items():
        v = float(v or 0)
        ln = catalog.get(k)
        if v <= 0 or not ln:
            continue
        attrs = {'line': k, 'item_code': ln['item_code'], 'label': ln['label']}
        if ln['section'] == 'staff':
            attrs['employment_type'] = ln['label']
        if rep['notes'].get(k):
            attrs['doc_no' if ln['needs_doc'] else 'equipment_no'] = rep['notes'][k]
        conn.execute(ins, base + (ln['fact_type'], v, ln['unit'], jdump(attrs), '基层填报', status))


def approve(conn, report_id: int, user: dict, comment: str | None = None) -> dict:
    rep = _load(conn, report_id)
    if rep['status'] != '已提交':
        raise ReportError(f'当前状态“{rep["status"]}”不能审核')
    conn.execute("UPDATE daily_report SET status='已审核', reviewer=?, reviewed_at=?, review_comment=? WHERE report_id=?",
                 (user['who'], now(), comment, report_id))
    conn.execute("UPDATE business_fact SET status='已审核' WHERE report_id=?", (report_id,))
    if rep['supersedes_report_id']:
        old = rep['supersedes_report_id']
        conn.execute("UPDATE daily_report SET status='已被更正' WHERE report_id=?", (old,))
        conn.execute("UPDATE business_fact SET status='已冲销' WHERE report_id=?", (old,))
        log(conn, '日报', old, '冲销', user['who'], f'被更正版本 {report_id} 替代')
    w = conn.execute('SELECT MAX(depth_end) d FROM daily_report WHERE well_id=? AND status=\'已审核\'', (rep['well_id'],)).fetchone()
    conn.execute('UPDATE well SET actual_depth=MAX(actual_depth, ?) WHERE well_id=?', (w['d'] or 0, rep['well_id']))
    log(conn, '日报', report_id, '通过', user['who'], comment)
    batch = engine.recalc(conn, [rep['well_id']], [rep['report_date'][:7]], '事实审核通过', user['who'])
    out = get_report(conn, report_id)
    out['batch'] = batch
    return out


def reject(conn, report_id: int, user: dict, comment: str) -> dict:
    rep = _load(conn, report_id)
    if rep['status'] != '已提交':
        raise ReportError(f'当前状态“{rep["status"]}”不能驳回')
    conn.execute("UPDATE daily_report SET status='驳回', reviewer=?, reviewed_at=?, review_comment=? WHERE report_id=?",
                 (user['who'], now(), comment, report_id))
    conn.execute("DELETE FROM business_fact WHERE report_id=? AND status='已提交'", (report_id,))
    log(conn, '日报', report_id, '驳回', user['who'], comment)
    return get_report(conn, report_id)


def correct(conn, report_id: int, user: dict) -> int:
    """审核后发现报错：复制为新版本草稿，审核通过后冲销原事实并重算。"""
    rep = _load(conn, report_id)
    if rep['status'] != '已审核':
        raise ReportError('只有已审核的日报需要走更正')
    pending = conn.execute("SELECT report_id FROM daily_report WHERE supersedes_report_id=? AND status IN ('草稿','已提交','驳回')",
                           (report_id,)).fetchone()
    if pending:
        return pending['report_id']
    rid = conn.execute(
        'INSERT INTO daily_report (well_id, team_id, report_date, version, status, phase, depth_start, depth_end, hours, lines, '
        "line_notes, reporter, supersedes_report_id) VALUES (?,?,?,?, '草稿', ?,?,?,?,?,?,?,?)",
        (rep['well_id'], rep['team_id'], rep['report_date'], rep['version'] + 1, rep['phase'], rep['depth_start'],
         rep['depth_end'], jdump(rep['hours']), jdump(rep['lines']), jdump(rep['notes']), user['who'], report_id)).lastrowid
    log(conn, '日报', rid, '发起更正', user['who'], f'更正版本 {rep["version"]} → {rep["version"] + 1}')
    return rid


def update_draft(conn, report_id: int, body: dict, user: dict) -> None:
    rep = _load(conn, report_id)
    if rep['status'] not in ('草稿', '驳回'):
        raise ReportError(f'当前状态“{rep["status"]}”不能修改')
    f = _fields(body, rep['phase'])
    conn.execute("UPDATE daily_report SET depth_start=?, depth_end=?, hours=?, lines=?, line_notes=?, status='草稿', reporter=? "
                 'WHERE report_id=?', f[:5] + (user['who'], report_id))


def catalog(conn) -> dict:
    """报量项目字典与不由基层填报的成本项目说明（供填报页和主数据维护展示）。"""
    return {'sections': report_lines.SECTIONS, 'work_conditions': WORK_CONDITIONS, 'lines': report_lines.lines(conn),
            'not_collected': report_lines.not_collected(conn)}
