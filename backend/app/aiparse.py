"""AI 规则解析（第一期）：依据文件 → 文本 → AI 拆条款、提规则草稿 → 系统校验 → “待确认”规则。

只产出草稿：AI 不能写已发布规则，确认、回算、影响预览、发布仍走 rules 的现有流程。
校验分两级：
  不采纳（不生成草稿，列出原因）：科目不在科目树、单价不是数字、引用的原文在文件里找不到（标不出出处的不采纳）、
                                  计价方式或触发事实不在系统字典里；写不成公式的条款列为“需人工计价”。
  标为低置信度（生成草稿，确认时逐字核对）：单位、条件维度或取值、报量项目、组合方式、计算粒度不合规的字段。
每次调用写一条 ai_task：文件指纹、模型与提示词版本、原始输出、生成 / 不采纳 / 人工计价的数量；采纳情况从规则状态与修改记录统计。
"""
import hashlib
import re
from datetime import date
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException

from . import ai, rules
from .db import db_path, jdump, jload, log, now

MAX_CHARS = 60000          # 第一期整篇送模型；超长文件截断并在任务里标注（分段解析放第二期）
DOC_TYPES = ['甲方合同', '内部服务合同', '外包服务合同', '补充协议', '公司制度', '内部结算价', '调价通知', '其他']
PRICING = ['单价', '包干', '比例', '定额日费']
COMBINE = ['互斥', '叠加']
GRANULARITY = ['逐日', '按井累计', '按阶段', '按期间']
BASE_FACTS = ['进尺', '日历天', '人员出勤', '工况时间']
CONTRACT_MODES = ['单井总价', '总包', '日费', '米费']   # 形象收入的计价模式
CATEGORICAL = {'市场': 'market', '区块': 'block', '甲方': 'client', '井别': 'well_category', '井型': 'well_type'}


# ---------- 依据文件：保存与文本提取 ----------

def _docs_dir() -> Path:
    d = db_path().parent / 'docs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_text(name: str, data: bytes) -> tuple[str, int]:
    """返回 (文本, 页数)。PDF 每页前加【第 n 页】，Word 含表格（价目表常在表格里），扫描件留到第三期 OCR。"""
    ext = Path(name).suffix.lower()
    if ext == '.pdf':
        from pypdf import PdfReader
        try:
            reader = PdfReader(BytesIO(data))
            pages = [p.extract_text() or '' for p in reader.pages]
        except Exception as e:   # noqa: BLE001 —— 文件损坏或加密
            raise HTTPException(422, f'PDF 无法读取：{e}') from e
        return '\n'.join(f'【第 {i} 页】\n{t.strip()}' for i, t in enumerate(pages, 1)), len(pages)
    if ext == '.docx':
        import docx
        try:
            d = docx.Document(BytesIO(data))
        except Exception as e:   # noqa: BLE001
            raise HTTPException(422, f'Word 文件无法读取：{e}') from e
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(' | '.join(cells))
        return '\n'.join(parts), 0
    if ext == '.doc':
        return _doc_text(data), 0
    if ext in ('.txt', '.md'):
        for enc in ('utf-8', 'gbk'):
            try:
                return data.decode(enc), 0
            except UnicodeDecodeError:
                continue
        raise HTTPException(422, '文本文件编码无法识别（请用 UTF-8 或 GBK）')
    raise HTTPException(415, '支持 PDF（文字版）、Word（.doc / .docx）、TXT、Markdown（.md）；扫描件 OCR 在第三期')


def _doc_text(data: bytes) -> str:
    """旧版 Word（.doc，二进制格式）：用 macOS 自带的 textutil 转纯文本，其次 antiword；表格单元格转为制表符分隔。"""
    import shutil
    import subprocess
    import tempfile
    if not (data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')) or data.lstrip().startswith(b'{\\rtf')):
        raise HTTPException(422, '不是有效的 Word 文件（.doc）：请确认文件未损坏，或另存为 .docx 后上传')
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / 'in.doc'
        src.write_bytes(data)
        for cmd in (['textutil', '-convert', 'txt', '-encoding', 'UTF-8', '-stdout', str(src)], ['antiword', '-w', '0', str(src)]):
            if not shutil.which(cmd[0]):
                continue
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=60)
            except subprocess.TimeoutExpired as e:
                raise HTTPException(422, 'Word 文件转换超时') from e
            if r.returncode == 0:
                lines = [ln.replace('\t', ' | ').strip() for ln in r.stdout.decode('utf-8', 'replace').splitlines()]
                return '\n'.join(ln for ln in lines if ln)
            raise HTTPException(422, 'Word 文件无法读取：可能已损坏或加密，请另存为 .docx 后上传')
    raise HTTPException(415, '服务器缺少 .doc 转换工具（macOS 自带 textutil；Linux 请安装 antiword），可先另存为 .docx 上传')


def upload(conn, name: str, data: bytes, meta: dict, user: dict) -> dict:
    if meta.get('doc_type') not in DOC_TYPES:
        raise HTTPException(422, '请选择文件类型')
    text, pages = extract_text(name, data)
    if len(re.sub(r'\s|【第 \d+ 页】', '', text)) < 20:
        raise HTTPException(422, '没有提取到文字：可能是扫描件或图片 PDF（OCR 在第三期支持），请上传文字版')
    digest = hashlib.sha256(data).hexdigest()
    dup = conn.execute('SELECT doc_id, doc_name FROM source_doc WHERE file_hash=?', ('sha256:' + digest,)).fetchone()
    if dup:
        raise HTTPException(409, f'同一文件已上传过（#{dup["doc_id"]} {dup["doc_name"]}），无需重复上传')
    path = _docs_dir() / f'{digest}{Path(name).suffix.lower()}'
    path.write_bytes(data)
    doc_id = conn.execute(
        'INSERT INTO source_doc (doc_type, doc_no, doc_name, counterparty, counterparty_type, file_hash, parse_status, file_name, '
        "file_path, full_text, page_count, valid_from, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?, '待解析', ?,?,?,?,?,?,?)",
        (meta['doc_type'], (meta.get('doc_no') or '').strip() or None, (meta.get('doc_name') or '').strip() or Path(name).stem,
         (meta.get('counterparty') or '').strip() or None, None, 'sha256:' + digest, name, str(path), text, pages,
         meta.get('valid_from') or None, user['who'], now())).lastrowid
    log(conn, '依据文件', doc_id, '上传', user['who'], f'{name}，{len(text)} 字')
    return doc_row(conn, doc_id)


def doc_row(conn, doc_id: int) -> dict:
    r = conn.execute('SELECT doc_id, doc_type, doc_no, doc_name, counterparty, file_hash, parse_status, file_name, page_count, '
                     'valid_from, uploaded_by, uploaded_at, LENGTH(full_text) chars FROM source_doc WHERE doc_id=?', (doc_id,)).fetchone()
    if not r:
        raise HTTPException(404, '依据文件不存在')
    d = dict(r)
    t = conn.execute('SELECT * FROM ai_task WHERE doc_id=? ORDER BY task_id DESC LIMIT 1', (doc_id,)).fetchone()
    d['last_task'] = task_row(conn, t) if t else None
    return d


def list_docs(conn) -> list[dict]:
    return [doc_row(conn, r['doc_id']) for r in conn.execute(
        'SELECT doc_id FROM source_doc WHERE file_path IS NOT NULL ORDER BY doc_id DESC LIMIT 100')]


# ---------- 提示词：系统字典随提示词下发，约束 AI 只用系统里的编码和取值 ----------

def vocab(conn) -> dict:
    items = [dict(r) for r in conn.execute(
        'SELECT item_code, item_name, direction FROM cost_item WHERE is_leaf=1 ORDER BY sort_no')]
    lines = [dict(r) for r in conn.execute('SELECT line_key, label, unit, fact_type FROM report_line WHERE enabled=1 ORDER BY sort_no')]
    facts = sorted({ln['fact_type'] for ln in lines} | set(BASE_FACTS))
    units = sorted({r['unit'] for r in conn.execute('SELECT unit FROM unit_of_measure')} | {ln['unit'] for ln in lines} | {'米', '队天', '天', '元', '井'})
    values = {dim: sorted({r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM well WHERE {col} IS NOT NULL AND {col}!=''")})
              for dim, col in CATEGORICAL.items()}
    values['开次'] = sorted({r[0] for r in conn.execute('SELECT DISTINCT phase FROM team_well_period')})
    values['队伍'] = sorted({r[0] for r in conn.execute('SELECT team_no FROM team')})
    values['钻机型号'] = sorted({r[0] for r in conn.execute('SELECT DISTINCT rig_type FROM team WHERE rig_type IS NOT NULL')})
    values['井号'] = sorted({r[0] for r in conn.execute('SELECT well_id FROM well WHERE well_id NOT IN (SELECT well_id FROM well_deleted)')})
    return {'items': items, 'lines': lines, 'facts': facts, 'units': units, 'values': values}


SYSTEM_PROMPT = """你是钻井单井经营管控系统的规则解析助手。任务：把合同、补充协议、制度、调价通知里的经济条款，拆成系统可执行的计价规则草稿。

硬性要求：
1. 只做解析，不计算金额。
2. 每条规则必须给出 evidence：从原文逐字摘录、能证明单价和计价方式的那一句（不要改写、不要省略中间文字）。摘不出原文的不要输出。
3. 科目 item_code、触发事实 fact_type、报量项目 line、单位 unit、条件维度和取值，只能从下面给的系统字典里选；字典里没有的，照实写并把对应 field_confidence 打低。
4. 写不成“数量 × 单价 / 基数 × 比例”公式的条款（如“据实结算”“另行协商”“按发票”），放进 manual，不要硬凑规则。
5. 金额单位为元，比例写成小数（3.1% 写 0.031）。日期写 YYYY-MM-DD。
6. 只输出一个 JSON 对象，不要任何解释文字。
7. 甲方合同（单井合同或框架合同）要识别形象收入（科目 R02）的计价模式，写在 contract_mode：
   - 单井总价（单井固定总价）或 总包：pricing_method 写 包干，fact_type 写 进尺，unit 写 井，unit_price 写合同总价（元，万元要换算成元），
     conditions 写 {"dimension": "井号", "operator": "等于", "value": 合同所指井号}；evidence 摘录写明总价的那一句。
     系统按 总价 ÷ 设计井深 折算米价，逐日按进尺确认收入，累计封顶为总价，你不用折算。
   - 日费：pricing_method 写 单价，fact_type 写 日历天，unit 写 天，unit_price 写日费单价；可用 开次 等条件区分不同日费。
   - 米费：pricing_method 写 单价，fact_type 写 进尺，unit 写 米，unit_price 写每米单价；可用 井深 等条件分段。
   其他科目的规则 contract_mode 写空字符串。

输出格式：
{
  "clauses": [
    {
      "clause_no": "条款号，如 第4条 或 3.1",
      "page": "页码，如 6；没有页码写空字符串",
      "text": "条款原文（可截取与计价相关的完整句子）",
      "rules": [
        {
          "name": "规则名称，简短",
          "item_code": "末级科目编码",
          "fact_type": "触发事实；比例类写空字符串",
          "line": "报量项目编码；不限定具体报量项目时写空字符串",
          "pricing_method": "单价 | 包干 | 比例 | 定额日费",
          "contract_mode": "单井总价 | 总包 | 日费 | 米费；不是形象收入写空字符串",
          "unit_price": 数字,
          "unit": "计价单位；比例类写 元",
          "base_item_code": "比例类的基数科目编码，其他写空字符串",
          "conditions": [{"dimension": "井号|市场|区块|甲方|井别|井型|井深|开次|队伍|钻机型号", "operator": "等于|属于|大于|小于", "value": "取值；属于用逗号分隔"}],
          "combine_mode": "互斥 | 叠加",
          "granularity": "逐日 | 按井累计 | 按阶段 | 按期间",
          "valid_from": "生效日期，原文没写则空字符串",
          "evidence": "原文逐字摘录",
          "confidence": 0 到 1,
          "field_confidence": {"科目": 0到1, "触发事实": 0到1, "计价方式": 0到1, "单价": 0到1, "适用条件": 0到1, "组合方式": 0到1, "计算粒度": 0到1}
        }
      ]
    }
  ],
  "manual": [{"clause_no": "条款号", "text": "原文", "reason": "为什么写不成公式"}]
}"""


def build_messages(conn, doc: dict, text: str) -> list[dict]:
    v = vocab(conn)
    dict_text = '\n'.join([
        '【末级科目】' + '；'.join(f"{i['item_code']} {i['item_name']}（{i['direction']}）" for i in v['items']),
        '【报量项目】' + '；'.join(f"{ln['line_key']} {ln['label']}（{ln['unit']}，事实类型 {ln['fact_type']}）" for ln in v['lines']),
        '【触发事实】' + '、'.join(v['facts']),
        '【计价单位】' + '、'.join(v['units']),
        '【条件取值】' + '；'.join(f"{k}：{'、'.join(map(str, vals))}" for k, vals in v['values'].items() if vals),
    ])
    head = (f"文件：{doc['doc_name']}（{doc['doc_type']}）；文号：{doc['doc_no'] or '无'}；对方单位：{doc['counterparty'] or '未填'}；"
            f"登记生效日：{doc['valid_from'] or '未填'}")
    return [{'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': f'系统字典：\n{dict_text}\n\n{head}\n\n文件全文：\n{text}'}]


# ---------- 校验 ----------

def _norm(s: str) -> str:
    return re.sub(r'\s+', '', s or '')


def find_span(text: str, ev: str) -> tuple[int, int] | None:
    """原文定位：先精确匹配，再忽略空白匹配（PDF 抽出的文字常带换行）。"""
    ev = (ev or '').strip()
    if len(_norm(ev)) < 4:
        return None
    i = text.find(ev)
    if i >= 0:
        return i, i + len(ev)
    pat = r'\s*'.join(re.escape(ch) for ch in _norm(ev))
    m = re.search(pat, text)
    return (m.start(), m.end()) if m else None


def _page_at(text: str, pos: int) -> str:
    marks = [m for m in re.finditer(r'【第 (\d+) 页】', text) if m.start() <= pos]
    return f'p.{marks[-1].group(1)}' if marks else ''


def _level(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return '低'
    return '高' if x >= 0.85 else '中' if x >= 0.6 else '低'


def check_rule(r: dict, v: dict, text: str) -> tuple[dict | None, str | None]:
    """返回 (清洗后的规则, 不采纳原因)。"""
    items = {i['item_code']: i for i in v['items']}
    lines = {ln['line_key']: ln for ln in v['lines']}
    fc = r.get('field_confidence') or {}
    levels = {k: _level(fc.get(k, r.get('confidence'))) for k in ('科目', '触发事实', '计价方式', '单价', '适用条件', '组合方式', '计算粒度')}
    code = str(r.get('item_code') or '').strip()
    if code not in items:
        return None, f'科目 {code or "（空）"} 不在科目树末级'
    method = str(r.get('pricing_method') or '').strip()
    if method not in PRICING:
        return None, f'计价方式“{method}”不在系统字典'
    try:
        price = float(r.get('unit_price'))
    except (TypeError, ValueError):
        return None, '单价不是数字'
    if price < 0:
        return None, '单价为负'
    span = find_span(text, r.get('evidence'))
    if not span:
        return None, '引用的原文在文件里找不到（标不出出处的不采纳）'
    fact = str(r.get('fact_type') or '').strip() or None
    base = str(r.get('base_item_code') or '').strip() or None
    if method == '比例':
        if base not in items:
            return None, f'比例类规则的基数科目 {base or "（空）"} 不在科目树'
        fact = None
    elif fact not in v['facts']:
        return None, f'触发事实“{fact or "（空）"}”不在系统字典'
    params = {}
    line = str(r.get('line') or '').strip()
    if line:
        if line in lines and lines[line]['fact_type'] == fact:
            params['line'] = line
        else:
            levels['触发事实'] = '低'
    unit = str(r.get('unit') or '').strip() or ('元' if method == '比例' else '')
    if line in lines and unit != lines[line]['unit']:
        levels['单价'] = '低'   # 计价单位与报量单位不一致，确认时核对
    elif unit not in v['units']:
        levels['单价'] = '低'
    conds = []
    for c in r.get('conditions') or []:
        dim, op, val = str(c.get('dimension') or ''), str(c.get('operator') or ''), str(c.get('value') or '').strip()
        if dim not in rules.DIMENSIONS or op not in rules.OPERATORS or not val:
            levels['适用条件'] = '低'
            continue
        allowed = v['values'].get(dim)
        if allowed and op in ('等于', '属于') and any(x.strip() not in map(str, allowed) for x in val.replace('，', ',').split(',')):
            levels['适用条件'] = '低'   # 取值在单井基础信息里没有，可能写错，也可能是新区块
        conds.append({'dimension': dim, 'operator': op, 'value': val})
    mode = str(r.get('contract_mode') or '').strip()
    if mode in CONTRACT_MODES:
        params['contract_mode'] = mode
        want = {'单井总价': ('包干', '进尺'), '总包': ('包干', '进尺'), '日费': ('单价', '日历天'), '米费': ('单价', '进尺')}[mode]
        if (method, fact) != want:
            levels['计价方式'] = '低'   # 计价模式与计价方式 / 触发事实对不上，确认时核对
    elif mode:
        levels['计价方式'] = '低'
    if method == '包干' and fact == '进尺':
        if not any(c['dimension'] == '井号' and c['operator'] == '等于' for c in conds):
            levels['适用条件'] = '低'   # 单井总价必须限定到井，否则会套到所有井上
        if not _amount_in(text[span[0]:span[1]], price):
            levels['单价'] = '低'
    combine = r.get('combine_mode') if r.get('combine_mode') in COMBINE else '互斥'
    if r.get('combine_mode') not in COMBINE:
        levels['组合方式'] = '低'
    gran = r.get('granularity') if r.get('granularity') in GRANULARITY else '逐日'
    if r.get('granularity') not in GRANULARITY:
        levels['计算粒度'] = '低'
    vf = str(r.get('valid_from') or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', vf):
        vf = ''
    try:
        conf = max(0.0, min(1.0, float(r.get('confidence'))))
    except (TypeError, ValueError):
        conf = 0.5
    if '低' in levels.values():
        conf = min(conf, 0.59)
    return {'name': str(r.get('name') or items[code]['item_name'])[:60], 'item_code': code, 'direction': items[code]['direction'],
            'fact_type': fact, 'pricing_method': method, 'unit_price': price, 'unit': unit, 'base_item_code': base,
            'params': params, 'conditions': conds, 'combine_mode': combine, 'granularity': gran, 'valid_from': vf,
            'span': span, 'confidence': conf, 'levels': levels}, None


def _amount_in(evidence: str, price: float) -> bool:
    """合同总价能否在摘录里找到（元或万元写法均可），找不到时提示确认人核对金额。"""
    nums = [float(x.replace(',', '')) for x in re.findall(r'\d[\d,]*(?:\.\d+)?', evidence)]
    return any(abs(n - price) < 0.5 or abs(n * 1e4 - price) < 0.5 for n in nums)


# ---------- 解析 ----------

def task_row(conn, t) -> dict:
    d = dict(t)
    for k in ('skipped', 'manual'):
        d[k] = jload(d.get(k), [])
    d.pop('output', None)
    st = {r['status']: r['n'] for r in conn.execute(
        'SELECT status, COUNT(*) n FROM economic_rule WHERE ai_task_id=? GROUP BY status', (d['task_id'],))}
    d['adoption'] = {
        'pending': st.get('待确认', 0) + st.get('草稿', 0) + st.get('回算中', 0) + st.get('待发布', 0),
        'published': st.get('已发布', 0) + st.get('已被替代', 0),
        'modified': conn.execute("SELECT COUNT(DISTINCT l.object_id) n FROM approval_log l JOIN economic_rule r ON l.object_type='规则' "
                                 "AND l.object_id=CAST(r.rule_id AS TEXT) WHERE r.ai_task_id=? AND l.action='修改'",
                                 (d['task_id'],)).fetchone()['n'],
        'rejected': conn.execute("SELECT COUNT(DISTINCT l.object_id) n FROM approval_log l JOIN economic_rule r ON l.object_type='规则' "
                                 "AND l.object_id=CAST(r.rule_id AS TEXT) WHERE r.ai_task_id=? AND l.action='驳回'",
                                 (d['task_id'],)).fetchone()['n'],
    }
    return d


def list_tasks(conn) -> list[dict]:
    return [task_row(conn, t) for t in conn.execute(
        'SELECT t.*, d.doc_name FROM ai_task t LEFT JOIN source_doc d ON d.doc_id=t.doc_id ORDER BY t.task_id DESC LIMIT 50')]


def parse(conn, doc_id: int, user: dict) -> dict:
    """调用 AI 解析一份依据文件。失败也记任务（返回 ok=False），由调用方提交事务后报错，留痕不丢。"""
    doc = conn.execute('SELECT * FROM source_doc WHERE doc_id=?', (doc_id,)).fetchone()
    if not doc or not doc['full_text']:
        raise HTTPException(404, '依据文件不存在或没有文本')
    doc = dict(doc)
    text = doc['full_text']
    truncated = len(text) > MAX_CHARS
    sent = text[:MAX_CHARS]
    cfg = ai.config()
    task_id = conn.execute(
        "INSERT INTO ai_task (task_type, doc_id, status, model, prompt_version, input_hash, input_chars, truncated, created_by, "
        "created_at) VALUES ('规则解析', ?, '进行中', ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, cfg['model'] or None, ai.PROMPT_VERSION, doc['file_hash'], len(sent), 1 if truncated else 0, user['who'], now())).lastrowid
    try:
        raw = ai.call_model(build_messages(conn, doc, sent))
        out = ai.parse_json(raw)
    except ai.AIError as e:
        conn.execute("UPDATE ai_task SET status='失败', error=?, finished_at=? WHERE task_id=?", (str(e), now(), task_id))
        conn.execute("UPDATE source_doc SET parse_status='解析失败' WHERE doc_id=?", (doc_id,))
        log(conn, 'AI任务', task_id, '失败', user['who'], str(e))
        return {'ok': False, 'error': str(e), 'task': task_row(conn, conn.execute('SELECT * FROM ai_task WHERE task_id=?', (task_id,)).fetchone())}

    v = vocab(conn)
    created, skipped = [], []
    n = 0
    for cl in out.get('clauses') or []:
        clause_no = str(cl.get('clause_no') or '').strip() or '未编号'
        for r in cl.get('rules') or []:
            clean, why = check_rule(r, v, text)
            if not clean:
                skipped.append({'clause_no': clause_no, 'name': str(r.get('name') or ''), 'reason': why})
                continue
            a, b = clean['span']
            page = _page_at(text, a) or (f"p.{cl.get('page')}" if str(cl.get('page') or '').strip() else '')
            pre = text[max(0, a - 80):a].split('\n')[-1]
            post = text[b:b + 80].split('\n')[0]
            clause_id = conn.execute('INSERT INTO clause (doc_id, clause_no, page_no, text_pre, text_hit, text_post) VALUES (?,?,?,?,?,?)',
                                     (doc_id, clause_no, page, pre, text[a:b], post)).lastrowid
            n += 1
            order = 3 if clean['pricing_method'] == '比例' else 1 if clean['direction'] == '收入' else 2
            rid = conn.execute(
                'INSERT INTO economic_rule (rule_code, version, name, item_code, clause_id, fact_type, pricing_method, base_item_code, '
                'calc_order, unit_price, unit, calc_params, combine_mode, granularity, priority, valid_from, status, origin, '
                "ai_confidence, field_levels, ai_task_id) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,0,?, '待确认', 'AI提取', ?,?,?)",
                (f'RL-AI{task_id}-{n:02d}', clean['name'], clean['item_code'], clause_id, clean['fact_type'], clean['pricing_method'],
                 clean['base_item_code'], order, clean['unit_price'], clean['unit'], jdump(clean['params']), clean['combine_mode'],
                 clean['granularity'], clean['valid_from'] or doc['valid_from'] or date.today().isoformat(),
                 clean['confidence'], jdump(clean['levels']), task_id)).lastrowid
            for c in clean['conditions']:
                conn.execute('INSERT INTO rule_condition (rule_id, dimension, operator, value) VALUES (?,?,?,?)',
                             (rid, c['dimension'], c['operator'], c['value']))
            log(conn, '规则', rid, 'AI 提取', 'AI 解析', f"任务 #{task_id} · {doc['doc_name']} {clause_no} · 置信度 {clean['confidence']:.2f}")
            created.append({'rule_id': rid, 'rule_code': f'RL-AI{task_id}-{n:02d}', 'name': clean['name'], 'item_code': clean['item_code'],
                            'confidence': clean['confidence'], 'low_fields': [k for k, x in clean['levels'].items() if x == '低']})
    manual = [{'clause_no': str(m.get('clause_no') or ''), 'text': str(m.get('text') or '')[:300], 'reason': str(m.get('reason') or '')}
              for m in out.get('manual') or []]
    conn.execute("UPDATE ai_task SET status='完成', output=?, draft_count=?, skipped=?, manual=?, finished_at=? WHERE task_id=?",
                 (raw[:200000], len(created), jdump(skipped), jdump(manual), now(), task_id))
    conn.execute("UPDATE source_doc SET parse_status='已解析' WHERE doc_id=?", (doc_id,))
    log(conn, 'AI任务', task_id, '完成', user['who'],
        f"{doc['doc_name']}：生成草稿 {len(created)} 条，不采纳 {len(skipped)} 条，需人工计价 {len(manual)} 条")
    return {'ok': True, 'created': created, 'skipped': skipped, 'manual': manual, 'truncated': truncated,
            'task': task_row(conn, conn.execute('SELECT * FROM ai_task WHERE task_id=?', (task_id,)).fetchone())}
