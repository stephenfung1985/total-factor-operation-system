"""填报 AI 核对留痕。

解析成功时写一条 ai_task，只保存建议值，不写日报、不写基础信息。
人保存草稿时把任务号挂到单据上；提交时按字段对比建议值与最终值，写入 ai_fill_trace。
建议值以服务端任务记录为准，不接受前端另报一份“建议值”。
"""
import hashlib

from . import ai
from .db import jdump, jload, log, now

PROMPT_VERSION = 'fill-parse-v1'
TASK_TYPE = {'日报': '日报解析', '新开井': '新开井解析'}
TABLE = {'日报': 'daily_report', '新开井': 'well_info_report'}

WELL_FIELD_LABEL = {
    'well_id': '井号', 'period': '归属年月', 'client': '甲方单位', 'block': '区块', 'captain': '队长',
    'rig_type': '钻机类型', 'market': '市场', 'well_category': '井别', 'well_type': '井型',
    'spud_sections': '设计开次', 'design_depth': '设计井深（米）',
}


class FillTraceError(Exception):
    pass


def text_of(v) -> str:
    """留痕展示用的文本。数字去掉无意义的小数尾零，空值一律空串。"""
    if v is None or isinstance(v, bool):
        return '' if v is None else ('1' if v else '0')
    if isinstance(v, (int, float)):
        x = float(v)
        if abs(x - round(x)) < 1e-6:
            return str(int(round(x)))
        return f'{x:.6f}'.rstrip('0').rstrip('.')
    return str(v).strip()


def _num_str(v) -> float | None:
    if not isinstance(v, str):
        return None
    s = v.strip().replace(',', '')
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def same(a, b) -> bool:
    """建议值与提交值是否相同。数字按数值比（4500 与 4500.0 相同），其余按文本。"""
    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a_num and b_num:
        return abs(float(a) - float(b)) < 1e-6
    if a_num and _num_str(b) is not None:
        return abs(float(a) - _num_str(b)) < 1e-6
    if b_num and _num_str(a) is not None:
        return abs(float(b) - _num_str(a)) < 1e-6
    return text_of(a) == text_of(b)


def _id_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = jload(raw, [])
    if not isinstance(raw, list):
        raise FillTraceError('AI 任务编号格式不正确')
    return raw


def record_extract(conn, user: dict, *, kind: str, name: str, data: bytes, text: str, truncated: bool,
                   suggestions: dict, labels: dict | None, notes: list[str],
                   well_id: str | None = None, report_date: str | None = None) -> int:
    """记一次填报解析。只写 ai_task，调用方仍只把建议值返回前端。"""
    cfg = ai.config()
    n = _field_count(kind, suggestions, labels or {})
    payload = {
        'kind': kind, 'team_id': user['team_id'], 'well_id': well_id, 'report_date': report_date,
        'file_name': (name or '文件')[:200], 'suggestions': suggestions, 'labels': labels or {}, 'notes': notes,
    }
    task_id = conn.execute(
        'INSERT INTO ai_task (task_type, status, model, prompt_version, input_hash, input_chars, truncated, output, '
        'draft_count, skipped, created_by, created_at, finished_at) '
        "VALUES (?, '完成', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (TASK_TYPE[kind], cfg['model'] or None, PROMPT_VERSION, 'sha256:' + hashlib.sha256(data).hexdigest(),
         len(text), 1 if truncated else 0, jdump(payload), n, jdump(notes), user['who'], now(), now())).lastrowid
    log(conn, 'AI任务', task_id, '完成', user['who'], f'{kind}解析 {payload["file_name"]}：建议 {n} 项，待人工核对后提交')
    return task_id


def bind_tasks(conn, raw, user: dict, kind: str, well_id: str | None = None, report_date: str | None = None) -> list[int]:
    """核对任务号：必须是本队、本类、已完成的解析，日报还要井号和日期对得上。"""
    ids: list[int] = []
    for i in _id_list(raw):
        if isinstance(i, bool) or not isinstance(i, int):
            raise FillTraceError('AI 任务编号不正确')
        if i in ids:
            continue
        t = conn.execute('SELECT * FROM ai_task WHERE task_id=?', (i,)).fetchone()
        if not t or t['task_type'] != TASK_TYPE[kind] or t['status'] != '完成':
            raise FillTraceError(f'AI 任务 #{i} 不存在，或不是本次填报的解析结果')
        payload = jload(t['output'], {})
        if not isinstance(payload, dict) or payload.get('team_id') != user.get('team_id'):
            raise FillTraceError(f'AI 任务 #{i} 不是本队的解析记录')
        if kind == '日报' and (payload.get('well_id') != well_id or payload.get('report_date') != report_date):
            raise FillTraceError(f'AI 任务 #{i} 是 {payload.get("well_id")} {payload.get("report_date")} 的解析，不能用于本张日报')
        ids.append(i)
    return ids


def attach(conn, kind: str, report_id: int, raw, user: dict, well_id: str | None = None,
           report_date: str | None = None) -> None:
    """把解析任务挂到草稿上。raw 为 None 表示本次保存不改挂接。"""
    if raw is None:
        return
    ids = bind_tasks(conn, raw, user, kind, well_id=well_id, report_date=report_date)
    conn.execute(f'UPDATE {TABLE[kind]} SET ai_task_ids=? WHERE report_id=?', (jdump(ids), report_id))


def snapshot_report(conn, rep: dict, user: dict) -> None:
    """日报提交时落字段留痕。没有挂解析任务则什么都不写。"""
    ids = rep.get('ai_task_ids') or []
    if not ids:
        return
    ids = bind_tasks(conn, ids, user, '日报', well_id=rep['well_id'], report_date=rep['report_date'])
    merged = _merge(conn, ids, '日报')
    rows = []
    for key, (task_id, label, suggested) in merged.items():
        submitted = _report_final(rep, key)
        rows.append({'task_id': task_id, 'key': key, 'label': label, 'suggested': suggested,
                     'submitted': submitted, 'modified': not same(suggested, submitted)})
    _write(conn, '日报', rep['report_id'], rows, user)


def snapshot_well(conn, rec: dict, user: dict) -> None:
    """新开井登记提交时落字段留痕。"""
    ids = rec.get('ai_task_ids') or []
    if not ids:
        return
    ids = bind_tasks(conn, ids, user, '新开井')
    merged = _merge(conn, ids, '新开井')
    rows = []
    for key, (task_id, label, suggested) in merged.items():
        submitted = _well_final(rec, key)
        rows.append({'task_id': task_id, 'key': key, 'label': label, 'suggested': suggested,
                     'submitted': submitted, 'modified': not same(suggested, submitted)})
    _write(conn, '新开井', rec['report_id'], rows, user)


def query(conn, object_type: str, object_id) -> dict:
    rows = [dict(r) for r in conn.execute(
        'SELECT id, capture_no, ai_task_id, object_type, object_id, field_key, field_label, suggested, submitted, '
        'modified, operator, submitted_at FROM ai_fill_trace WHERE object_type=? AND object_id=? ORDER BY capture_no, id',
        (object_type, str(object_id)))]
    for r in rows:
        r['modified'] = bool(r['modified'])
    latest_no = max((r['capture_no'] for r in rows), default=None)
    latest = [r for r in rows if r['capture_no'] == latest_no]
    task_ids = sorted({r['ai_task_id'] for r in rows})
    tasks = []
    if task_ids:
        marks = ','.join('?' * len(task_ids))
        tasks = [dict(t) for t in conn.execute(
            f'SELECT task_id, task_type, status, model, prompt_version, created_by, created_at, finished_at '
            f'FROM ai_task WHERE task_id IN ({marks}) ORDER BY task_id', task_ids)]
    return {
        'object_type': object_type, 'object_id': str(object_id), 'fields': rows, 'latest': latest, 'tasks': tasks,
        'summary': {
            'fields': len(latest), 'modified': sum(1 for r in latest if r['modified']),
            'unchanged': sum(1 for r in latest if not r['modified']),
            'captures': len({r['capture_no'] for r in rows}),
        },
    }


def recent(conn, limit: int = 50) -> list[dict]:
    """管理端：最近若干次提交的留痕摘要。"""
    groups = conn.execute(
        'SELECT object_type, object_id, capture_no, MAX(submitted_at) submitted_at, MAX(operator) operator, '
        'COUNT(*) fields, SUM(modified) modified FROM ai_fill_trace '
        'GROUP BY object_type, object_id, capture_no ORDER BY capture_no DESC LIMIT ?', (limit,)).fetchall()
    out = []
    for g in groups:
        d = dict(g)
        d['modified'] = int(d['modified'] or 0)
        d['object_id'] = str(d['object_id'])
        if d['object_type'] == '日报':
            r = conn.execute(
                'SELECT d.well_id, d.report_date, d.version, t.name team FROM daily_report d '
                'JOIN team t ON t.team_id=d.team_id WHERE d.report_id=?', (d['object_id'],)).fetchone()
        else:
            r = conn.execute(
                'SELECT r.well_id, r.period, r.version, t.name team FROM well_info_report r '
                'JOIN team t ON t.team_id=r.team_id WHERE r.report_id=?', (d['object_id'],)).fetchone()
        if r:
            d.update(dict(r))
        out.append(d)
    return out


def _write(conn, object_type: str, object_id, rows: list[dict], user: dict) -> None:
    if not rows:
        return
    cap = conn.execute('SELECT COALESCE(MAX(capture_no), 0) + 1 n FROM ai_fill_trace').fetchone()['n']
    at = now()
    who = user['who']
    for r in rows:
        conn.execute(
            'INSERT INTO ai_fill_trace (capture_no, ai_task_id, object_type, object_id, field_key, field_label, '
            'suggested, submitted, modified, operator, submitted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (cap, r['task_id'], object_type, str(object_id), r['key'], r['label'], text_of(r['suggested']),
             text_of(r['submitted']), 1 if r['modified'] else 0, who, at))
    changed = sum(1 for r in rows if r['modified'])
    tasks = '、'.join(f'#{i}' for i in sorted({r['task_id'] for r in rows}))
    log(conn, object_type, object_id, 'AI核对留痕', who, f'任务 {tasks}：{len(rows)} 项，人工修改 {changed} 项')


def _tasks(conn, ids: list[int]) -> list:
    out = []
    for i in ids:
        t = conn.execute('SELECT * FROM ai_task WHERE task_id=?', (i,)).fetchone()
        if t:
            out.append(t)
    return out


def _merge(conn, ids: list[int], kind: str) -> dict:
    """后挂上的任务覆盖同一字段；每个字段记住给出建议的那次任务。"""
    merged: dict = {}
    for t in _tasks(conn, ids):
        payload = jload(t['output'], {})
        sugg = payload.get('suggestions') or {}
        labels = payload.get('labels') or {}
        for key, label, value in (_iter_report(sugg, labels) if kind == '日报' else _iter_well(sugg)):
            merged[key] = (t['task_id'], label, value)
    return merged


def _field_count(kind: str, suggestions: dict, labels: dict) -> int:
    src = _iter_report(suggestions, labels) if kind == '日报' else _iter_well(suggestions)
    return sum(1 for _ in src)


def _iter_report(sugg: dict, labels: dict):
    if 'depth_start' in sugg and sugg.get('depth_start') is not None:
        yield 'depth_start', '起始井深（米）', sugg['depth_start']
    if 'depth_end' in sugg and sugg.get('depth_end') is not None:
        yield 'depth_end', '结束井深（米）', sugg['depth_end']
    for h, v in (sugg.get('hours') or {}).items():
        yield f'hours.{h}', f'工况·{h}（小时）', v
    for k, v in (sugg.get('lines') or {}).items():
        yield f'lines.{k}', labels.get(k) or k, v
    for k, v in (sugg.get('doc_nos') or {}).items():
        yield f'doc.{k}', f'{labels.get(k) or k}·单据号', v


def _iter_well(sugg: dict):
    from . import wellinfo
    staff_label = dict(wellinfo.STAFF)
    for k, v in (sugg.get('fields') or {}).items():
        yield f'fields.{k}', WELL_FIELD_LABEL.get(k, k), v
    for k, v in (sugg.get('milestones') or {}).items():
        yield f'milestones.{k}', wellinfo.MILESTONE_LABEL.get(k, k), v
    for k, v in (sugg.get('staff') or {}).items():
        yield f'staff.{k}', staff_label.get(k, k), v


def _report_final(rep: dict, key: str):
    if key == 'depth_start':
        return rep.get('depth_start')
    if key == 'depth_end':
        return rep.get('depth_end')
    kind, rest = key.split('.', 1)
    if kind == 'hours':
        return (rep.get('hours') or {}).get(rest, 0)
    if kind == 'lines':
        lines = rep.get('lines') or {}
        return lines[rest] if rest in lines else ''
    if kind == 'doc':
        notes = rep.get('notes') or {}
        return notes[rest] if rest in notes else ''
    return ''


def _well_final(rec: dict, key: str):
    kind, rest = key.split('.', 1)
    if kind == 'fields':
        return rec.get(rest)
    if kind == 'milestones':
        return (rec.get('milestones') or {}).get(rest) or ''
    if kind == 'staff':
        staff = rec.get('staff') or {}
        return staff[rest] if rest in staff else 0
    return ''
