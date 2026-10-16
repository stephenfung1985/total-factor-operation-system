"""单井基础信息填报：基层钻井队按井登记，随施工进度更新节点与人员。

字段与《单井基础信息表》一致，按数据类型分段：基本信息（文本 / 选择）、井深（数值）、施工节点（日期时间）、
人员（整数）、自动计算（周期、状态、滞后，只读）。
提交后自动汇总到单井基础信息，并同步主数据：well、team、well_milestone、team_well_period；
井的开次、市场等变化会影响规则匹配，因此有事实的期间自动重算。
"""
import re
from datetime import date, datetime, timedelta

from . import engine
from .db import jdump, jload, log, now

SECTIONS = ['一开', '二开', '三开', '四开', '五开']
MARKETS = ['西北', '长庆', '大港', '云南']
CATEGORIES = ['开发井', '评价井', '预探井', '注采气井']
WELL_TYPES = ['直井', '定向井', '水平井']
RIGS = ['ZJ40J', 'ZJ50D', 'ZJ50J', 'ZJ50L', 'ZJ70D', 'ZJ70J', 'ZJ90D']
STAFF = [('合同化', '合同化人数'), ('市场化', '市场化人数'), ('第三方', '第三方用工人数')]
COMPLETION_LAG_LIMIT = 2.0   # 完井时间填报时限（天）
TEXT_FIELDS = ['market', 'captain', 'rig_type', 'client', 'block', 'well_category', 'well_type']


def milestone_keys() -> list[str]:
    keys = ['搬安', '砸导管']
    for k in range(1, 6):
        keys += [f'开钻{k}', f'完钻{k}']
    return keys + ['完钻', '完井']


MILESTONE_LABEL = {'搬安': '搬安（平移）时间', '砸导管': '砸导管时间', '完钻': '完钻时间', '完井': '完井时间'}
for _k, _s in enumerate(SECTIONS, start=1):
    MILESTONE_LABEL[f'开钻{_k}'] = f'{_s}时间'
    MILESTONE_LABEL[f'完钻{_k}'] = f'{_s}完钻'


class InfoError(Exception):
    pass


# ---------- 解析与派生 ----------

def _dt(s) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    s = str(s).strip().replace('T', ' ')
    for f in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    raise InfoError(f'时间格式不正确：{s}')


def _fmt(d: datetime | None) -> str | None:
    return d.strftime('%Y-%m-%d %H:%M') if d else None


def _days(a: datetime | None, b: datetime | None) -> float | None:
    return None if a is None or b is None else (b - a).total_seconds() / 86400


def normalize(body: dict) -> dict:
    rec = {
        'well_id': str(body.get('well_id') or '').strip(),
        'period': str(body.get('period') or '').strip(),
        'spud_sections': int(body.get('spud_sections') or 0),
        'design_depth': float(body.get('design_depth') or 0),
    }
    for f in TEXT_FIELDS:
        rec[f] = re.sub(r'\s+', ' ', str(body.get(f) or '')).strip()
    ms = body.get('milestones') or {}
    rec['milestones'] = {}
    for k in milestone_keys():
        try:
            rec['milestones'][k] = _fmt(_dt(ms.get(k)))
        except InfoError:
            rec['milestones'][k] = None
            rec.setdefault('_bad_times', []).append(MILESTONE_LABEL[k])
    staff = body.get('staff') or {}
    rec['staff'] = {}
    for k, _ in STAFF:
        try:
            rec['staff'][k] = int(float(staff.get(k) or 0))
        except (TypeError, ValueError):
            rec['staff'][k] = -1
    return rec


def derive(rec: dict, at: datetime | None = None) -> dict:
    """周期、状态与滞后：未提交的草稿按“现在提交”预估。"""
    at = at or datetime.now()
    m = {k: _dt(v) for k, v in (rec.get('milestones') or {}).items() if v}
    spud, td, comp = m.get('开钻1'), m.get('完钻'), m.get('完井')
    if comp and td:
        status = '完井'
    elif td:
        status = '完钻'
    elif spud:
        status = '正钻'
    else:
        status = '计划'
    registered = _dt(rec.get('registered_at')) or at
    if not spud:
        reg_lag_days, reg_lag = None, '未开钻'
    else:
        reg_lag_days = _days(spud, registered)
        reg_lag = f'滞后 {reg_lag_days:.2f} 天' if reg_lag_days > 0 else '未滞后'
    comp_lag_days, comp_lag = None, None
    if comp:
        reported = _dt(rec.get('completion_reported_at')) or at
        comp_lag_days = _days(comp, reported)
        comp_lag = f'滞后 {comp_lag_days:.2f} 天' if comp_lag_days > COMPLETION_LAG_LIMIT else '未滞后'
    sections = {}
    for k in range(1, 6):
        d = _days(m.get(f'开钻{k}'), m.get(f'完钻{k}'))
        if d is not None:
            sections[k] = round(d, 2)
    staff = rec.get('staff') or {}
    return {
        'pre_days': round(_days(m.get('搬安'), spud) or 0, 4),
        'drill_days': round(_days(spud, td) or 0, 4),
        'completion_days': round(_days(td, comp) or 0, 4),
        'status': status, 'reg_lag': reg_lag, 'reg_lag_days': reg_lag_days,
        'comp_lag': comp_lag, 'comp_lag_days': comp_lag_days,
        'section_days': sections, 'staff_total': sum(v for v in staff.values() if v > 0),
    }


# ---------- 校验 ----------

def validate(conn, rec: dict, team_id: str, at: datetime | None = None) -> list[dict]:
    at = at or datetime.now()
    out = []

    def block(rule, msg):
        out.append({'level': '阻断', 'rule': rule, 'message': msg})

    def warn(rule, msg):
        out.append({'level': '警告', 'rule': rule, 'message': msg})

    wid = rec['well_id']
    if not wid:
        block('井号', '井号必填：单井井号是唯一身份识别码')
    elif len(wid) > 40 or re.search(r'\s', wid):
        block('井号', '井号不能含空格，长度不超过 40')
    else:
        other = conn.execute(
            'SELECT 1 FROM team_well_period WHERE well_id=? AND team_id!=? UNION '
            "SELECT 1 FROM well_info_report WHERE well_id=? AND team_id!=? AND status='已提交'", (wid, team_id, wid, team_id)).fetchone()
        mine = conn.execute('SELECT 1 FROM team_well_period WHERE well_id=? AND team_id=?', (wid, team_id)).fetchone()
        if other and not mine:
            block('井号', f'井号 {wid} 已由其他队伍登记；换队施工请联系企划财务部')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', rec['period'] or ''):
        block('归属期间', '请选择年份和月份')
    labels = {'market': '市场分布', 'rig_type': '钻机类型', 'client': '甲方单位', 'block': '区块',
              'well_category': '井别', 'well_type': '井型', 'captain': '队长'}
    for f, label in labels.items():
        if not rec[f]:
            block('必填', f'{label}必填')
    n = rec['spud_sections']
    if not 1 <= n <= 5:
        block('开次', '开次必须在 1–5 之间')
    if rec['design_depth'] <= 0:
        block('设计井深', '设计井深必须大于 0')
    elif rec['design_depth'] > 12000:
        warn('设计井深', f'设计井深 {rec["design_depth"]:g} 米超出常见范围，请核对')
    for k, label in STAFF:
        if rec['staff'][k] < 0:
            block('人员', f'{label}必须是不小于 0 的整数')
    for label in rec.get('_bad_times', []):
        block('时间格式', f'{label}格式不正确')

    m = {k: _dt(v) for k, v in rec['milestones'].items() if v}
    for k in range(1, 6):
        if k > max(n, 0) and (m.get(f'开钻{k}') or m.get(f'完钻{k}')):
            block('开次', f'设计开次为 {n}，不能填写{SECTIONS[k - 1]}时间')
        if m.get(f'完钻{k}') and not m.get(f'开钻{k}'):
            block('节点缺失', f'填了{SECTIONS[k - 1]}完钻，缺少{SECTIONS[k - 1]}时间')
        if k > 1 and m.get(f'开钻{k}') and not m.get(f'完钻{k - 1}'):
            block('节点缺失', f'填了{SECTIONS[k - 1]}时间，缺少{SECTIONS[k - 2]}完钻')
        a, b = m.get(f'开钻{k}'), m.get(f'完钻{k}')
        if a and b and b <= a:
            block('时间顺序', f'{SECTIONS[k - 1]}完钻必须晚于{SECTIONS[k - 1]}时间')
    if m.get('完钻') and not m.get('开钻1'):
        block('节点缺失', '填了完钻时间，缺少一开时间')
    if m.get('完井') and not m.get('完钻'):
        block('节点缺失', '填了完井时间，缺少完钻时间')
    seq = [k for k in milestone_keys() if m.get(k)]
    for a, b in zip(seq, seq[1:]):
        if a.startswith('开钻') and b == '完钻' + a[2:]:
            continue   # 同一开次的开钻与完钻已在上面检查
        if m[b] < m[a]:
            block('时间顺序', f'{MILESTONE_LABEL[b]}（{_fmt(m[b])}）早于{MILESTONE_LABEL[a]}（{_fmt(m[a])}）')
    late = [MILESTONE_LABEL[k] for k in seq if m[k] > at + timedelta(hours=1)]
    if late:
        warn('未来时间', f'{"、".join(late)}晚于当前时间，请核对')
    if m.get('完钻'):
        done = [k for k in range(1, 6) if m.get(f'完钻{k}')]
        if len(done) < n:
            warn('开次完钻', f'已填完钻时间，但只有 {len(done)} 个开次完钻（设计 {n} 开）')
        elif done and m[f'完钻{done[-1]}'] != m['完钻']:
            warn('完钻时间', f'完钻时间与{SECTIONS[done[-1] - 1]}完钻不一致')
    d = derive(rec, at)
    if d['reg_lag_days'] is not None and d['reg_lag_days'] > 0:
        warn('登记滞后', f'登记晚于一开 {d["reg_lag_days"]:.2f} 天：应在开钻前登记')
    if d['comp_lag_days'] is not None and d['comp_lag_days'] > COMPLETION_LAG_LIMIT:
        warn('完井填报滞后', f'完井时间填报滞后 {d["comp_lag_days"]:.2f} 天（时限 {COMPLETION_LAG_LIMIT:g} 天）')
    return out


# ---------- 读写 ----------

def _row_to_rec(r) -> dict:
    d = dict(r)
    d['milestones'] = jload(d['milestones'])
    d['staff'] = jload(d['staff'])
    return d


def _load(conn, rid: int, team_id: str | None = None) -> dict:
    r = conn.execute('SELECT * FROM well_info_report WHERE report_id=?', (rid,)).fetchone()
    if not r or (team_id and r['team_id'] != team_id):
        raise InfoError('基础信息记录不存在')
    return _row_to_rec(r)


def _team(conn, team_id: str) -> dict:
    return dict(conn.execute('SELECT * FROM team WHERE team_id=?', (team_id,)).fetchone())


def view(conn, rec: dict, team_id: str) -> dict:
    rec = dict(rec)
    rec['team_no'] = _team(conn, rec['team_id'])['team_no']
    rec['derived'] = derive(rec)
    rec['checks'] = validate(conn, rec, team_id) if rec['status'] in ('新建', '草稿') else []
    rec['history'] = [dict(x) for x in conn.execute(
        'SELECT report_id, version, status, submitted_at, creator, source, return_comment, returned_by, returned_at '
        'FROM well_info_report WHERE well_id=? AND team_id=? ORDER BY version DESC', (rec['well_id'], rec['team_id']))]         if rec.get('well_id') else []
    return rec


def current(conn, well_id: str, team_id: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM well_info_report WHERE well_id=? AND team_id=? AND status IN ('草稿', '已提交') "
        "ORDER BY CASE status WHEN '草稿' THEN 0 ELSE 1 END, version DESC LIMIT 1", (well_id, team_id)).fetchone()
    return _row_to_rec(r) if r else None


def template(conn, user: dict) -> dict:
    t = _team(conn, user['team_id'])
    return {
        'report_id': None, 'well_id': '', 'team_id': user['team_id'], 'version': 1, 'status': '新建',
        'period': date.today().strftime('%Y-%m'), 'market': '', 'captain': t['captain'] or '', 'rig_type': t['rig_type'] or '',
        'client': '', 'block': '', 'well_category': '', 'well_type': '', 'spud_sections': 2, 'design_depth': 0,
        'milestones': {k: None for k in milestone_keys()}, 'staff': {k: 0 for k, _ in STAFF},
        'creator': user['name'], 'registered_at': None, 'completion_reported_at': None, 'submitted_at': None,
    }


def my_wells(conn, user: dict) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT * FROM well_info_report r WHERE team_id=? AND status IN ('草稿', '已提交') "
            "AND well_id NOT IN (SELECT well_id FROM well_deleted) AND version=("
            "SELECT MAX(version) FROM well_info_report x WHERE x.well_id=r.well_id AND x.team_id=r.team_id "
            "AND x.status IN ('草稿', '已提交')) ORDER BY CASE status WHEN '草稿' THEN 0 ELSE 1 END, well_id", (user['team_id'],)):
        rec = _row_to_rec(r)
        d = derive(rec)
        out.append({'report_id': rec['report_id'], 'well_id': rec['well_id'], 'version': rec['version'], 'status': rec['status'],
                    'well_status': d['status'], 'reg_lag': d['reg_lag'], 'comp_lag': d['comp_lag'],
                    'submitted_at': rec['submitted_at'], 'returned': bool(rec.get('return_comment')) and rec['status'] == '草稿',
                    'return_comment': rec.get('return_comment')})
    return out


def save_draft(conn, body: dict, user: dict) -> int:
    rec = normalize(body)
    team_id = user['team_id']
    fields = (rec['well_id'], rec['period'], rec['market'], rec['captain'], rec['rig_type'], rec['client'], rec['block'],
              rec['well_category'], rec['well_type'], rec['spud_sections'], rec['design_depth'],
              jdump(rec['milestones']), jdump(rec['staff']))
    rid = body.get('report_id')
    if rid:
        old = _load(conn, rid, team_id)
        if old['status'] != '草稿':
            raise InfoError('已提交的记录不能直接修改，请点“更新基础信息”生成新版本')
        if old['well_id'] != rec['well_id'] and (old['version'] > 1 or old['registered_at']):
            raise InfoError('已登记井的井号不能修改')
        conn.execute('UPDATE well_info_report SET well_id=?, period=?, market=?, captain=?, rig_type=?, client=?, block=?, '
                     'well_category=?, well_type=?, spud_sections=?, design_depth=?, milestones=?, staff=?, updated_at=? '
                     'WHERE report_id=?', fields + (now(), rid))
        return rid
    if not rec['well_id']:
        raise InfoError('请先填写井号')
    cur = current(conn, rec['well_id'], team_id)
    if cur and cur['status'] == '草稿':
        body = {**body, 'report_id': cur['report_id']}
        return save_draft(conn, body, user)
    if cur:
        raise InfoError(f'{rec["well_id"]} 已登记，请在列表中选择该井后点“更新基础信息”')
    rid = conn.execute(
        'INSERT INTO well_info_report (well_id, team_id, version, status, period, market, captain, rig_type, client, block, '
        'well_category, well_type, spud_sections, design_depth, milestones, staff, creator, created_at, updated_at) '
        "VALUES (?,?,1,'草稿',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rec['well_id'], team_id) + fields[1:] + (user['name'], now(), now())).lastrowid
    log(conn, '基础信息', rid, '保存草稿', user['who'])
    return rid


def revise(conn, rid: int, user: dict) -> int:
    rec = _load(conn, rid, user['team_id'])
    if rec['status'] != '已提交':
        raise InfoError('只有已提交的记录可以更新')
    draft = current(conn, rec['well_id'], rec['team_id'])
    if draft and draft['status'] == '草稿':
        return draft['report_id']
    nid = _copy_draft(conn, rid, user['name'])
    log(conn, '基础信息', nid, '更新', user['who'], f'由版本 {rec["version"]} 更新')
    return nid


def _copy_draft(conn, rid: int, creator: str) -> int:
    """以已提交版本为底稿生成下一版本草稿。"""
    return conn.execute(
        'INSERT INTO well_info_report (well_id, team_id, version, status, period, market, captain, rig_type, client, block, '
        'well_category, well_type, spud_sections, design_depth, milestones, staff, creator, created_at, updated_at, '
        "registered_at, completion_reported_at, supersedes_report_id) SELECT well_id, team_id, version+1, '草稿', period, market, "
        'captain, rig_type, client, block, well_category, well_type, spud_sections, design_depth, milestones, staff, ?, ?, ?, '
        'registered_at, completion_reported_at, report_id FROM well_info_report WHERE report_id=?',
        (creator, now(), now(), rid)).lastrowid


def submit(conn, rid: int, user: dict, at: datetime | None = None, recalc: bool = True, keep_times: bool = False) -> dict:
    """提交即生效：汇总到单井基础信息并同步主数据。keep_times：导入时沿用原表的填报时间与完井填报日期。"""
    rec = _load(conn, rid, user['team_id'])
    if rec['status'] != '草稿':
        raise InfoError(f'当前状态“{rec["status"]}”不能提交')
    at = at or datetime.now()
    prev = conn.execute("SELECT * FROM well_info_report WHERE well_id=? AND team_id=? AND status='已提交' ORDER BY version DESC LIMIT 1",
                        (rec['well_id'], rec['team_id'])).fetchone()
    if not keep_times:
        rec['registered_at'] = rec['registered_at'] or (prev['registered_at'] if prev else None) or _fmt(at)
        comp = rec['milestones'].get('完井')
        prev_comp = jload(prev['milestones']).get('完井') if prev else None
        if not comp:
            rec['completion_reported_at'] = None
        elif prev and (comp != prev_comp or not rec['completion_reported_at']):
            rec['completion_reported_at'] = _fmt(at)
        elif not prev:
            rec['completion_reported_at'] = rec['completion_reported_at'] or _fmt(at)
    checks = validate(conn, rec, rec['team_id'], at)
    blocks = [c['message'] for c in checks if c['level'] == '阻断']
    if blocks:
        raise InfoError('存在阻断项，不能提交：' + '；'.join(blocks))
    if prev:
        conn.execute("UPDATE well_info_report SET status='已被更新' WHERE report_id=?", (prev['report_id'],))
    conn.execute("UPDATE well_info_report SET status='已提交', submitted_at=?, registered_at=?, completion_reported_at=? "
                 'WHERE report_id=?', (_fmt(at), rec['registered_at'], rec['completion_reported_at'], rid))
    batch = _apply(conn, rec, user, recalc)
    log(conn, '基础信息', rid, '导入' if keep_times else '提交', user['who'], '已汇总到单井基础信息')
    out = view(conn, _load(conn, rid), rec['team_id'])
    out['batch'] = batch
    return out


def _segments(rec: dict) -> list[tuple[str, str, str | None]]:
    """由节点推导施工时段：钻前（搬安起）→ 一开 → 二开 …，末段到完井为止。"""
    m = {k: _dt(v) for k, v in rec['milestones'].items() if v}
    points = []
    if m.get('搬安'):
        points.append(('钻前', m['搬安'].date()))
    starts = [(SECTIONS[k - 1], m[f'开钻{k}'].date()) for k in range(1, 6) if m.get(f'开钻{k}')]
    if not points and not starts:
        points.append(('钻前', _dt(rec['registered_at']).date()))
    points += starts
    end_all = m['完井'].date() if m.get('完井') else None
    segs = []
    for i, (phase, start) in enumerate(points):
        end = points[i + 1][1] - timedelta(days=1) if i + 1 < len(points) else end_all
        if end is not None and end < start:
            if i + 1 < len(points):
                continue
            end = start
        segs.append((phase, start.isoformat(), end.isoformat() if end else None))
    return segs


def _apply(conn, rec: dict, user: dict, recalc: bool) -> dict | None:
    d = derive(rec)
    wid, tid = rec['well_id'], rec['team_id']
    conn.execute('DELETE FROM well_deleted WHERE well_id=?', (wid,))   # 重新登记即恢复已删除的井
    w = conn.execute('SELECT status FROM well WHERE well_id=?', (wid,)).fetchone()
    status = w['status'] if w and w['status'] == '已结算' else d['status']
    if w:
        conn.execute('UPDATE well SET market=?, block=?, well_category=?, well_type=?, spud_sections=?, design_depth=?, client=?, '
                     'status=? WHERE well_id=?', (rec['market'], rec['block'], rec['well_category'], rec['well_type'],
                                                 rec['spud_sections'], rec['design_depth'], rec['client'], status, wid))
    else:
        conn.execute('INSERT INTO well VALUES (?,?,?,?,?,?,?,0,?,0,?)',
                     (wid, rec['market'], rec['block'], rec['well_category'], rec['well_type'], rec['spud_sections'],
                      rec['design_depth'], rec['client'], status))
    conn.execute('UPDATE team SET captain=?, rig_type=? WHERE team_id=?', (rec['captain'], rec['rig_type'], tid))
    conn.execute('DELETE FROM well_milestone WHERE well_id=? AND team_id=?', (wid, tid))
    for k in milestone_keys():
        v = rec['milestones'].get(k)
        if not v:
            continue
        kind = re.match(r'(开钻|完钻)(\d)$', k)
        milestone, sec = (kind.group(1), int(kind.group(2))) if kind else ({'完钻': '完钻', '完井': '完井'}.get(k, k), None)
        if k == '完钻':
            milestone = '全井完钻'
        conn.execute('INSERT INTO well_milestone (well_id, team_id, milestone, section_no, occurred_at, info_report_id) '
                     'VALUES (?,?,?,?,?,?)', (wid, tid, milestone, sec, v, rec['report_id']))
    conn.execute('DELETE FROM team_well_period WHERE well_id=? AND team_id=?', (wid, tid))
    for phase, start, end in _segments(rec):
        conn.execute('INSERT INTO team_well_period (team_id, well_id, phase, start_at, end_at) VALUES (?,?,?,?,?)',
                     (tid, wid, phase, start, end))
    if not recalc:
        return None
    periods = sorted({r['p'] for r in conn.execute(
        "SELECT DISTINCT substr(fact_date,1,7) p FROM business_fact WHERE well_id=? AND status='已审核'", (wid,))})
    return engine.recalc(conn, [wid], periods, '基础信息变更', user['who']) if periods else None


# ---------- 汇总（企划财务部） ----------

def summary(conn) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT r.*, t.team_no FROM well_info_report r JOIN team t ON t.team_id=r.team_id WHERE r.status='已提交' "
            "AND r.well_id NOT IN (SELECT well_id FROM well_deleted) "
            "AND r.submitted_at=(SELECT MAX(submitted_at) FROM well_info_report x WHERE x.well_id=r.well_id AND x.status='已提交') "
            'ORDER BY r.well_id'):
        rec = _row_to_rec(r)
        rec['derived'] = derive(rec)
        rec['pending_return'] = _pending(conn, rec['well_id'], rec['team_id'])
        out.append(rec)
    return out


# ---------- 基础信息库（按角色裁剪字段） ----------

LIB_BASE = [('well_id', '井号'), ('team_no', '队号'), ('market', '市场'), ('block', '区块'), ('well_category', '井别'),
            ('well_type', '井型'), ('spud_sections', '开次'), ('design_depth', '设计井深（米）'), ('status', '状态'),
            ('client', '甲方单位'), ('rig_type', '钻机类型'), ('pre_days', '钻前周期（天）'), ('drill_days', '钻井周期（天）'),
            ('completion_days', '完井周期（天）')]
LIB_FULL = [('captain', '队长'), ('staff_total', '人员合计'), ('reg_lag', '登记滞后'), ('comp_lag', '完井填报滞后'),
            ('submitted_at', '提交时间')]
LIB_STAFF = [('staff_total', '人员合计')]


def library_columns(user: dict) -> list[tuple[str, str]]:
    """基础信息库可见字段：管理层、本队看全部；专业部室只看井的基础与周期信息，人事部室另可看人员数量。"""
    if user['role'] in ('finance', 'team'):
        return LIB_BASE + LIB_FULL
    return LIB_BASE + (LIB_STAFF if user.get('dept') == '人事' else [])


def library(conn, user: dict) -> dict:
    cols = library_columns(user)
    keys = [k for k, _ in cols]
    show_staff = 'staff_total' in keys
    rows = []
    for r in summary(conn):
        if user['role'] == 'team' and r['team_id'] != user['team_id']:
            continue
        d = r['derived']
        flat = {**r, **d, 'team_no': _team(conn, r['team_id'])['team_no']}
        row = {k: flat.get(k) for k in keys}
        row['milestones'] = {k: v for k, v in r['milestones'].items() if v}
        if show_staff:
            row['staff'] = {k: v for k, v in r['staff'].items() if v}
        rows.append(row)
    return {'columns': [{'key': k, 'label': l} for k, l in cols], 'rows': rows, 'role': user['role'], 'scope': user['scope_label'],
            'staff_visible': show_staff}


def _pending(conn, well_id: str, team_id: str) -> dict | None:
    r = conn.execute("SELECT report_id, return_comment, returned_by, returned_at FROM well_info_report WHERE well_id=? "
                     "AND team_id=? AND status='草稿' AND return_comment IS NOT NULL", (well_id, team_id)).fetchone()
    return dict(r) if r else None


def latest(conn, well_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM well_info_report WHERE well_id=? AND status='已提交' ORDER BY submitted_at DESC LIMIT 1",
                     (well_id,)).fetchone()
    return _row_to_rec(r) if r else None


def well_detail(conn, well_id: str) -> dict:
    rec = latest(conn, well_id)
    if not rec:
        raise InfoError('该井尚未提交基础信息')
    rec['derived'] = derive(rec)
    rec['team_no'] = _team(conn, rec['team_id'])['team_no']
    rec['pending_return'] = _pending(conn, well_id, rec['team_id'])
    rec['segments'] = [dict(x) for x in conn.execute(
        'SELECT p.phase, p.start_at, p.end_at, t.name team FROM team_well_period p JOIN team t ON t.team_id=p.team_id '
        'WHERE p.well_id=? ORDER BY p.start_at', (well_id,))]
    rec['versions'] = [dict(x) for x in conn.execute(
        'SELECT r.report_id, r.version, r.status, r.submitted_at, r.creator, r.source, r.return_comment, t.name team '
        'FROM well_info_report r '
        "JOIN team t ON t.team_id=r.team_id WHERE r.well_id=? AND r.status!='草稿' ORDER BY r.submitted_at DESC", (well_id,))]
    return rec


EXCEL_HEADERS = (['井号', '年份', '月份', '市场分布', '队号', '队长', '钻机类型', '甲方单位', '区块', '井别', '井型', '开次',
                  '设计井深（米）', '钻前周期（天）', '钻井周期（天）', '完井周期（天）']
                 + [MILESTONE_LABEL[k] for k in milestone_keys()]
                 + [h for _, h in STAFF] + ['状态', '填报时间', '登记滞后天数', '完井时间填报日期', '完井时间填报滞后天数', '创建人'])


def excel_row(rec: dict) -> list:
    d = rec['derived']
    y, mth = (rec['period'] or '-').split('-')
    row = [rec['well_id'], y, mth, rec['market'], rec['team_no'], rec['captain'], rec['rig_type'], rec['client'], rec['block'],
           rec['well_category'], rec['well_type'], str(rec['spud_sections']), rec['design_depth'],
           round(d['pre_days'], 4), round(d['drill_days'], 4), round(d['completion_days'], 4)]
    row += [_dt(rec['milestones'].get(k)) for k in milestone_keys()]
    row += [rec['staff'].get(k, 0) for k, _ in STAFF]
    row += [d['status'], _dt(rec['registered_at']), d['reg_lag'], _dt(rec['completion_reported_at']), d['comp_lag'], rec['creator']]
    return row


# ---------- 退回队伍修改（企划财务部） ----------

def return_to_team(conn, well_id: str, comment: str, user: dict) -> dict:
    """已汇总的基础信息有误时退回填报队伍：生成带退回原因的草稿，队伍修改后重新提交；
    重新提交前，汇总表与主数据仍按当前已提交版本。"""
    comment = (comment or '').strip()
    if not comment:
        raise InfoError('请填写退回原因')
    cur = latest(conn, well_id)
    if not cur:
        raise InfoError('该井尚未提交基础信息')
    draft = conn.execute("SELECT report_id FROM well_info_report WHERE well_id=? AND team_id=? AND status='草稿'",
                         (well_id, cur['team_id'])).fetchone()
    rid = draft['report_id'] if draft else _copy_draft(conn, cur['report_id'], cur['creator'])
    conn.execute('UPDATE well_info_report SET return_comment=?, returned_by=?, returned_at=? WHERE report_id=?',
                 (comment, user['who'], now(), rid))
    log(conn, '基础信息', rid, '退回', user['who'], comment)
    return well_detail(conn, well_id)


def pending_returns(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        'SELECT r.report_id, r.well_id, r.version, r.source, r.return_comment, r.returned_by, r.returned_at, r.updated_at, '
        "t.team_no, t.name team FROM well_info_report r JOIN team t ON t.team_id=r.team_id WHERE r.status='草稿' "
        'AND r.return_comment IS NOT NULL ORDER BY r.returned_at DESC, r.well_id')]


# ---------- 按《单井基础信息表》格式导入（企划财务部） ----------

DERIVED_HEADERS = {'钻前周期（天）', '钻井周期（天）', '完井周期（天）', '状态', '登记滞后天数', '完井时间填报滞后天数'}
REQUIRED_HEADERS = [h for h in EXCEL_HEADERS if h not in DERIVED_HEADERS]
COMPARE = ['period', 'market', 'captain', 'rig_type', 'client', 'block', 'well_category', 'well_type', 'spud_sections',
           'design_depth', 'milestones', 'staff', 'registered_at', 'completion_reported_at']


def _s(x) -> str:
    if x is None:
        return ''
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return str(x).strip()


def _num(x) -> float:
    try:
        return float(_s(x).replace(',', '') or 0)
    except ValueError:
        return -1


def _from_sheet(v: dict) -> tuple[dict, dict]:
    """原表一行 → 填报字段。周期、状态、滞后由系统重算，不取表中的值。"""
    y, m = _s(v.get('年份')), _s(v.get('月份'))
    period = f'{int(y):04d}-{int(m):02d}' if y.isdigit() and m.isdigit() else ''
    sections = _num(v.get('开次'))
    body = {
        'well_id': _s(v.get('井号')), 'period': period, 'market': _s(v.get('市场分布')), 'captain': _s(v.get('队长')),
        'rig_type': _s(v.get('钻机类型')), 'client': _s(v.get('甲方单位')), 'block': _s(v.get('区块')),
        'well_category': _s(v.get('井别')), 'well_type': _s(v.get('井型')),
        'spud_sections': int(sections) if sections > 0 else 0,
        'design_depth': _num(v.get('设计井深（米）')),
        'milestones': {k: v.get(MILESTONE_LABEL[k]) for k in milestone_keys()},
        'staff': {k: v.get(h) for k, h in STAFF},
    }
    meta = {'team_no': _s(v.get('队号')), 'creator': _s(v.get('创建人')) or '导入',
            'registered_at': v.get('填报时间'), 'completion_reported_at': v.get('完井时间填报日期')}
    return body, meta


def _same(cur: dict, rec: dict) -> bool:
    return all(cur.get(k) == rec.get(k) for k in COMPARE)


def import_rows(conn, rows: list[dict], user: dict, commit: bool) -> dict:
    """逐行跑与线上填报相同的校验：
    通过的直接汇总（新增，或对已登记井生成新版本）；有阻断项的生成草稿退回对应队伍修改；
    井号或队号为空、文件内重复、他队已登记、队伍有未提交草稿的跳过。commit=False 只预览，不写库。"""
    at = datetime.now()
    seen = set()
    results = []
    for r in rows:
        body, meta = _from_sheet(r['values'])
        wid, team_no = body['well_id'], meta['team_no']
        rec = normalize(body)
        time_errors = []
        try:
            rec['registered_at'] = _fmt(_dt(meta['registered_at'])) or _fmt(at)
            rec['completion_reported_at'] = _fmt(_dt(meta['completion_reported_at']))
        except InfoError as e:
            time_errors.append(str(e))
            rec['registered_at'], rec['completion_reported_at'] = _fmt(at), None
        team = conn.execute('SELECT * FROM team WHERE team_no=?', (team_no,)).fetchone() if team_no else None
        tid = team['team_id'] if team else 'T' + team_no
        checks = validate(conn, rec, tid, at) if team_no else []
        checks = [{'level': '阻断', 'rule': '时间格式', 'message': m} for m in time_errors] + checks
        skip = None
        if not wid:
            skip = '井号为空'
        elif not team_no:
            skip = '队号为空'
        elif wid in seen:
            skip = '文件内井号重复，以第一次出现的行为准'
        else:
            owner = [c['message'] for c in checks if c['rule'] == '井号' and c['level'] == '阻断']
            skip = owner[0] if owner else None
        seen.add(wid)
        cur = current(conn, wid, tid) if wid and team else None
        if not skip and cur and cur['status'] == '草稿':
            skip = '该井有队伍尚未提交的草稿，请先处理'
        blocks = [c['message'] for c in checks if c['level'] == '阻断']
        if skip:
            action = '跳过'
        elif blocks:
            action = '退回队伍'
        elif cur and _same(cur, rec):
            action = '无变化'
        else:
            action = '更新' if cur else '新增'
        results.append({'row': r['row'], 'well_id': wid, 'team_no': team_no, 'action': action, 'new_team': bool(team_no) and not team,
                        'status': derive(rec, at)['status'], 'skip': skip, 'checks': checks})
        if not commit or action not in ('新增', '更新', '退回队伍'):
            continue
        if not team:
            conn.execute('INSERT INTO team (team_id, team_no, name, rig_type, unit, captain) VALUES (?,?,?,?, NULL, ?)',
                         (tid, team_no, f'{team_no} 钻井队', rec['rig_type'] or None, rec['captain'] or None))
        ver = conn.execute('SELECT COALESCE(MAX(version), 0) v FROM well_info_report WHERE well_id=? AND team_id=?',
                           (wid, tid)).fetchone()['v'] + 1
        rid = conn.execute(
            'INSERT INTO well_info_report (well_id, team_id, version, status, period, market, captain, rig_type, client, block, '
            'well_category, well_type, spud_sections, design_depth, milestones, staff, creator, created_at, updated_at, '
            "registered_at, completion_reported_at, supersedes_report_id, source) "
            "VALUES (?,?,?,'草稿',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'导入')",
            (wid, tid, ver, rec['period'], rec['market'], rec['captain'], rec['rig_type'], rec['client'], rec['block'],
             rec['well_category'], rec['well_type'], rec['spud_sections'], rec['design_depth'], jdump(rec['milestones']),
             jdump(rec['staff']), meta['creator'], now(), now(), rec['registered_at'], rec['completion_reported_at'],
             cur['report_id'] if cur else None)).lastrowid
        if action == '退回队伍':
            conn.execute('UPDATE well_info_report SET return_comment=?, returned_by=?, returned_at=? WHERE report_id=?',
                         ('导入校验未通过：' + '；'.join(blocks), user['who'], now(), rid))
            log(conn, '基础信息', rid, '退回', user['who'], f'导入第 {r["row"]} 行校验未通过')
        else:
            submit(conn, rid, {'team_id': tid, 'name': meta['creator'], 'who': user['who']}, at=at, keep_times=True)
    count: dict[str, int] = {}
    for x in results:
        count[x['action']] = count.get(x['action'], 0) + 1
    return {'rows': results, 'count': count, 'committed': commit,
            'new_teams': sorted({x['team_no'] for x in results if x['new_team'] and x['action'] != '跳过'})}
