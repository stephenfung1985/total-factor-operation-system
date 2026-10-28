"""填报端 AI 解析：上传文档 / 表格 → 文本 → AI 按填报字段抽取 → 系统校验 → 返回“建议值”。

只返回建议值，不落库：前端把建议值填入表单并标出，基层核对、修改后仍走原有的校验、保存、提交流程。
AI 不接触数据库；字典（市场、井别、报量项目等）随提示词下发，不在字典内的取值一律丢弃并在提示里说明。
"""
import csv
import re
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import HTTPException

from . import ai, aiparse, reports, wellinfo

MAX_CHARS = 40000
SHEET_EXTS = ('.xlsx', '.xlsm', '.csv', '.tsv')
FILE_EXTS = ('.pdf', '.doc', '.docx', '.txt', '.md') + SHEET_EXTS


def file_text(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    if ext in ('.xlsx', '.xlsm'):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(BytesIO(data), data_only=True, read_only=True)
        except Exception as e:   # noqa: BLE001
            raise HTTPException(422, f'Excel 无法读取：{e}') from e
        parts = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = ['' if v is None else (v.strftime('%Y-%m-%d %H:%M') if hasattr(v, 'strftime') else str(v)).strip() for v in row]
                if any(cells):
                    rows.append(' | '.join(cells).rstrip(' |'))
            if rows:
                parts.append(f'【工作表 {ws.title}】\n' + '\n'.join(rows))
        text = '\n'.join(parts)
    elif ext in ('.csv', '.tsv'):
        raw = None
        for enc in ('utf-8-sig', 'gbk'):
            try:
                raw = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raise HTTPException(422, '表格文件编码无法识别（请用 UTF-8 或 GBK）')
        delim = '\t' if ext == '.tsv' else ','
        text = '\n'.join(' | '.join(c.strip() for c in row) for row in csv.reader(StringIO(raw), delimiter=delim) if any(row))
    elif ext in FILE_EXTS:
        text, _ = aiparse.extract_text(name, data)
    else:
        raise HTTPException(415, '支持 Excel（.xlsx）、CSV、Word（.doc / .docx）、PDF（文字版）、TXT')
    if len(re.sub(r'\s|[|【】]', '', text)) < 4:
        raise HTTPException(422, '没有提取到内容：可能是扫描件或图片，请上传文字版或表格')
    return text


def _call(messages: list[dict]) -> dict:
    if not ai.enabled():
        raise HTTPException(503, '未配置 AI 服务（管理员需在服务器设置 TFOS_AI_BASE_URL 和 TFOS_AI_MODEL），可继续手工填报')
    try:
        return ai.parse_json(ai.call_model(messages))
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e


def _trunc(text: str, notes: list[str]) -> str:
    if len(text) > MAX_CHARS:
        notes.append(f'文件内容过长，仅解析前 {MAX_CHARS} 字')
        return text[:MAX_CHARS]
    return text


def _num(v):
    if isinstance(v, str):
        v = re.sub(r'[,\s，米mhH小时天人]', '', v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_SYSTEM = ('你是钻井队填报助手。从用户给出的文档或表格内容中抽取填报字段。只抽取文档里明确写出的内容，没有写的字段不要输出，'
           '不要猜测、不要计算。只输出一个 JSON 对象，不要任何解释。')


# ---------- 新开井登记 ----------

def _dt_text(v) -> str | None:
    if not v:
        return None
    try:
        d = wellinfo._dt(str(v).replace('年', '-').replace('月', '-').replace('日', ' ').replace('时', ':').replace('点', ':').strip(' :'))
    except wellinfo.InfoError:
        return None
    return wellinfo._fmt(d)


def extract_wellinfo(conn, name: str, data: bytes) -> dict:
    text = file_text(name, data)
    notes: list[str] = []
    text = _trunc(text, notes)
    ms_desc = {k: wellinfo.MILESTONE_LABEL[k] for k in wellinfo.milestone_keys()}
    prompt = (
        '抽取新开井登记信息，输出 JSON：\n'
        '{"fields":{"well_id":"井号","period":"归属年月 YYYY-MM","client":"甲方单位","block":"区块","captain":"队长","rig_type":"钻机类型",\n'
        f' "market":"市场，只能是 {"/".join(wellinfo.MARKETS)}","well_category":"井别，只能是 {"/".join(wellinfo.CATEGORIES)}",\n'
        f' "well_type":"井型，只能是 {"/".join(wellinfo.WELL_TYPES)}","spud_sections":"设计开次，1-5 的整数","design_depth":"设计井深（米）"}},\n'
        ' "milestones":{节点键:"YYYY-MM-DD HH:MM"},  // 节点键与含义：' + '；'.join(f'{k}={v}' for k, v in ms_desc.items()) + '\n'
        ' "staff":{"合同化":人数,"市场化":人数,"第三方":人数}}\n\n文档内容：\n' + text)
    out = _call([{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': prompt}])
    raw_f, raw_m, raw_s = (out.get('fields') or {}), (out.get('milestones') or {}), (out.get('staff') or {})
    fields: dict = {}
    for k, label in (('well_id', '井号'), ('client', '甲方单位'), ('block', '区块'), ('captain', '队长'), ('rig_type', '钻机类型')):
        v = re.sub(r'\s+', ' ', str(raw_f.get(k) or '')).strip()
        if v:
            fields[k] = v
    for k, label, opts in (('market', '市场', wellinfo.MARKETS), ('well_category', '井别', wellinfo.CATEGORIES), ('well_type', '井型', wellinfo.WELL_TYPES)):
        v = str(raw_f.get(k) or '').strip()
        if not v:
            continue
        hit = next((o for o in opts if o == v), None) or next((o for o in opts if o in v or v in o), None)
        if hit:
            fields[k] = hit
        else:
            notes.append(f'{label}“{v}”不在系统选项内，已忽略')
    p = re.match(r'^(\d{4})\D{0,2}(\d{1,2})', str(raw_f.get('period') or ''))
    if p and 1 <= int(p.group(2)) <= 12:
        fields['period'] = f'{p.group(1)}-{int(p.group(2)):02d}'
    n = _num(raw_f.get('spud_sections'))
    if n is not None and 1 <= n <= 5:
        fields['spud_sections'] = int(n)
    n = _num(raw_f.get('design_depth'))
    if n is not None and n > 0:
        fields['design_depth'] = n
    milestones = {}
    for k, v in raw_m.items():
        if k not in ms_desc or not v:
            continue
        t = _dt_text(v)
        if t:
            milestones[k] = t
        else:
            notes.append(f'{ms_desc[k]}“{v}”时间格式无法识别，已忽略')
    staff = {}
    for k, _ in wellinfo.STAFF:
        n = _num(raw_s.get(k))
        if n is not None and n >= 0:
            staff[k] = int(n)
    if not (fields or milestones or staff):
        raise HTTPException(422, '没有从文件中识别到可填写的内容，请确认文件是新开井登记相关资料')
    return {'fields': fields, 'milestones': milestones, 'staff': staff, 'notes': notes, 'chars': len(text)}


# ---------- 成本日报 ----------

def extract_report(conn, name: str, data: bytes, well_id: str, rdate: str, team_id: str) -> dict:
    text = file_text(name, data)
    notes: list[str] = []
    text = _trunc(text, notes)
    defs = [d for d in reports.line_defs(conn, well_id, rdate, team_id) if d['priced']]
    by_key = {d['key']: d for d in defs}
    conds = reports.WORK_CONDITIONS
    prompt = (
        f'抽取 {well_id} 井 {rdate} 当天的成本日报数量，输出 JSON：\n'
        '{"depth_start":起始井深米,"depth_end":结束井深米,\n'
        f' "hours":{{工况:小时数}},  // 工况只能是：{"、".join(conds)}\n'
        ' "lines":{报量项目键:数量},  // 报量项目键只能从下表选；数量为当天用量\n'
        ' "notes":{报量项目键:"设备编号/签证单号/单据号"}}\n'
        f'文档含多天数据时只取 {rdate} 当天；找不到当天数据则输出空 JSON。\n\n报量项目表（键 | 名称 | 单位）：\n'
        + '\n'.join(f'{d["key"]} | {d["label"]}{"（" + d["group"] + "）" if d["group"] != d["label"] else ""} | {d["unit"]}' for d in defs)
        + '\n\n文档内容：\n' + text)
    out = _call([{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': prompt}])
    res: dict = {'hours': {}, 'lines': {}, 'doc_nos': {}}
    ds, de = _num(out.get('depth_start')), _num(out.get('depth_end'))
    if ds is not None and de is not None and de >= ds >= 0 and de > 0:
        res['depth_start'], res['depth_end'] = ds, de
    elif ds is not None or de is not None:
        notes.append('起止井深不完整或结束井深小于起始井深，已忽略')
    for h, v in (out.get('hours') or {}).items():
        n = _num(v)
        if h not in conds:
            notes.append(f'工况“{h}”不在系统工况内，已忽略')
        elif n is not None and 0 <= n <= 24:
            res['hours'][h] = n
    for k, v in (out.get('lines') or {}).items():
        n = _num(v)
        if k not in by_key:
            notes.append(f'报量项目“{k}”本井当天不参与费用计算或不存在，已忽略')
        elif n is not None and n > 0:
            res['lines'][k] = n
    for k, v in (out.get('notes') or {}).items():
        if k in res['lines'] and str(v or '').strip():
            res['doc_nos'][k] = str(v).strip()
    if not (res['hours'] or res['lines'] or 'depth_end' in res):
        raise HTTPException(422, f'没有从文件中识别到 {rdate} 的日报数据，请确认文件内容或日期')
    total = sum(res['hours'].values())
    if res['hours'] and abs(total - 24) > 1e-6:
        notes.append(f'识别到的工况时间合计 {total:g} 小时，不是 24 小时，请核对')
    res.update(notes=notes, labels={k: by_key[k]['label'] for k in res['lines']}, chars=len(text))
    return res
