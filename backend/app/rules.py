"""规则维护：草稿 → 待确认 → 回算中 → 待发布 → 已发布 → 已被替代 / 已废止。

费用规则入库分三级，后端按角色拒绝越权，不能由同一人走完全程：
  普通账户（管理端）起草、修改草稿并提交；AI 只生成草稿，不代为提交；
  归口部室确认或退回本部室规则（确认时冲突检查 + 历史井回算，超阈值留在待确认）；
  系统管理员做影响预览、登记依据后发布；发布后有误由系统管理员回滚。
任一步退回都回到草稿，由普通账户修改后重新提交。
普通账户也可手工新建草稿（AI 未配置时同样可以）。新建必须挂到已有依据文件上的条款，
含 AI 标为「需人工计价」的条款，避免事后金额对不上原文。
已发布规则只增不改：调价时由普通账户复制为新版本草稿，再走三级流程；旧版本失效日由新版本推导。
归口部室不能提交、发布，也不能确认其他部室的规则。
"""
import re
from datetime import date, timedelta

from fastapi import HTTPException

from . import engine
from .db import jdump, jload, log, money, now

STEPS = ['草稿', '待确认', '回算中', '待发布', '已发布']
EDITABLE = ('草稿',)   # 提交后锁定，费用管理部室退回后才能再改
DIMENSIONS = ['井号', '市场', '区块', '甲方', '井别', '井型', '井深', '开次', '队伍', '钻机型号']
OPERATORS = ['等于', '属于', '大于', '小于']
PRICING = ['单价', '包干', '比例', '定额日费']
COMBINE = ['互斥', '叠加']
# 「按井」是已入库规则用过的粒度；「按井累计」是解析字典里的取值。引擎尚未按这两种粒度单独计算。
GRANULARITY = ['逐日', '按井', '按井累计', '按阶段', '按期间']


def _row(conn, rule_id: int) -> dict:
    r = conn.execute('SELECT r.*, i.item_name, i.owner_dept FROM economic_rule r JOIN cost_item i ON i.item_code=r.item_code '
                     'WHERE r.rule_id=?', (rule_id,)).fetchone()
    if not r:
        raise HTTPException(404, '规则不存在')
    return dict(r)


def _visible(user: dict, r: dict) -> bool:
    if user['role'] in ('finance', 'cost', 'admin'):
        return True
    if user['role'] == 'dept':
        from .depts import covers
        return covers(user.get('dept'), r.get('owner_dept'), r.get('item_name'))
    return False


def _submitter(user: dict) -> bool:
    return user['role'] == 'finance'


def _reviewer(user: dict, r: dict | None = None) -> bool:
    """归口部室确认本部室规则。不传规则时只看角色，传了再核对归口。"""
    if user['role'] != 'dept':
        return False
    if r is None:
        return True
    from .depts import covers
    return covers(user.get('dept'), r.get('owner_dept'), r.get('item_name'))


def _publisher(user: dict) -> bool:
    return user['role'] == 'admin'


def list_rules(conn, user: dict) -> list[dict]:
    out = []
    for r in conn.execute('SELECT r.*, i.item_name, i.owner_dept, c.clause_no, d.doc_name FROM economic_rule r '
                          'JOIN cost_item i ON i.item_code=r.item_code LEFT JOIN clause c ON c.clause_id=r.clause_id '
                          'LEFT JOIN source_doc d ON d.doc_id=c.doc_id ORDER BY r.rule_id DESC'):
        r = dict(r)
        if not _visible(user, r):
            continue
        levels = jload(r['field_levels'])
        need = sum(1 for v in levels.values() if v != '高')
        conf = f"置信度 {r['ai_confidence']:.2f} · {need} 个字段需核对" if r['ai_confidence'] is not None else r['origin']
        out.append({'rule_id': r['rule_id'], 'rule_code': r['rule_code'], 'version': r['version'], 'name': r['name'],
                    'item_code': r['item_code'], 'item_name': r['item_name'], 'status': r['status'], 'doc': r['doc_name'] or '—',
                    'clause_no': r['clause_no'] or '', 'conf': conf, 'origin': r['origin'], 'owner_dept': r['owner_dept']})
    return out


def detail(conn, user: dict, rule_id: int) -> dict:
    r = _row(conn, rule_id)
    if not _visible(user, r):
        raise HTTPException(404, '规则不存在或不在数据范围内')
    conds = engine.rule_conditions(conn, rule_id)
    params = jload(r['calc_params'])
    levels = jload(r['field_levels'])
    cond_text = '；'.join(f"{c['dimension']} {c['operator']} {c['value']}" for c in conds)
    for fr in params.get('factor_rules', []):
        cond_text += ('；' if cond_text else '') + f"{fr['dimension']} {fr['operator']} {fr['value']} 时系数 {fr['factor']}"
    price = f"{r['unit_price'] * 100:.2f}%" if r['pricing_method'] == '比例' else f"{r['unit_price']:,.4f} 元/{r['unit']}"
    if engine.is_lump_sum(r):
        price = f"合同总价 {r['unit_price']:,.0f} 元（÷ 设计井深折算米价，逐日按进尺确认，累计封顶为总价）"
    method = r['pricing_method'] + (f" · {params['contract_mode']}" if params.get('contract_mode') else '')
    fields = [('科目', f"{r['item_code']} {r['item_name']}"), ('触发事实', r['fact_type'] or f"比例基数 {r['base_item_code']}"),
              ('计价方式', method), ('单价', price), ('适用条件', cond_text or '无（全部适用）'),
              ('组合方式', r['combine_mode']), ('计算粒度', r['granularity'])]
    clause = None
    if r['clause_id']:
        c = conn.execute('SELECT c.*, d.doc_name, d.doc_no, d.doc_type, d.file_hash FROM clause c JOIN source_doc d '
                         'ON d.doc_id=c.doc_id WHERE clause_id=?', (r['clause_id'],)).fetchone()
        clause = {'doc': c['doc_name'], 'doc_type': c['doc_type'], 'no': c['clause_no'], 'page': c['page_no'] or '',
                  'pre': c['text_pre'], 'hit': c['text_hit'], 'post': c['text_post'], 'hash': c['file_hash']}
    tests = [dict(t) for t in conn.execute('SELECT * FROM rule_test WHERE rule_id=? ORDER BY test_id', (rule_id,))]
    versions = [dict(v) for v in conn.execute(
        'SELECT rule_id, version, status, unit_price, valid_from, valid_to, published_at FROM economic_rule WHERE rule_code=? '
        'ORDER BY version', (r['rule_code'],))]
    logs = [dict(x) for x in conn.execute(
        "SELECT action, operator, operated_at, comment FROM approval_log WHERE object_type='规则' AND object_id=? ORDER BY id",
        (str(rule_id),))]
    hits = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) a FROM economic_event WHERE rule_id=? AND status='有效'",
                        (rule_id,)).fetchone()
    return {
        'rule': {k: r[k] for k in ('rule_id', 'rule_code', 'version', 'name', 'item_code', 'item_name', 'status', 'origin',
                                   'ai_confidence', 'unit_price', 'unit', 'combine_mode', 'granularity', 'valid_from', 'valid_to',
                                   'pricing_method', 'fact_type', 'base_item_code', 'owner_dept', 'priority')},
        'params': params, 'conditions': conds,
        'fields': [{'label': a, 'value': b, 'level': levels.get(a, '高')} for a, b in fields],
        'clause': clause, 'tests': tests, 'versions': versions, 'logs': logs,
        'hits': {'count': hits['n'], 'amount': money(hits['a'])},
        'can_edit': _submitter(user) and r['status'] in EDITABLE,
        'can_submit': _submitter(user) and r['status'] == '草稿',
        'can_confirm': _reviewer(user, r) and r['status'] == '待确认',
        'can_publish': _publisher(user) and r['status'] == '待发布',
        'can_reject': _reviewer(user, r) and r['status'] in ('待确认', '回算中', '待发布'),
        'can_new_version': _submitter(user) and r['status'] == '已发布',
        'can_rollback': _publisher(user) and r['status'] == '已发布',
        'change_basis': r.get('change_basis'),
        'owner_dept': r['owner_dept'],
        'options': _editor_options(conn),
    }


def _need(conn, user, rule_id, allowed: bool, message: str) -> dict:
    r = _row(conn, rule_id)
    if not _visible(user, r):
        raise HTTPException(404, '规则不存在或不在数据范围内')
    if not allowed:
        raise HTTPException(403, message)
    return r


def _need_submitter(conn, user, rule_id) -> dict:
    return _need(conn, user, rule_id, _submitter(user), '只有普通账户可以提交或修改规则')


def _need_reviewer(conn, user, rule_id) -> dict:
    """越出本部室也是 403，不把别人的规则说成不存在。"""
    r = _row(conn, rule_id)
    if not _reviewer(user, r):
        if user['role'] == 'dept':
            raise HTTPException(403, '只能确认或退回本部室的规则')
        raise HTTPException(403, '只有归口部室可以确认或退回本部室规则')
    return r


def _need_publisher(conn, user, rule_id) -> dict:
    return _need(conn, user, rule_id, _publisher(user), '只有系统管理员可以发布、预览或回滚规则')


def _leaves(conn) -> dict:
    return {r['item_code']: dict(r) for r in conn.execute(
        'SELECT item_code, item_name, direction FROM cost_item WHERE is_leaf=1 ORDER BY sort_no')}


def _facts(conn) -> set[str]:
    facts = {r[0] for r in conn.execute(
        "SELECT DISTINCT fact_type FROM report_line WHERE fact_type IS NOT NULL AND fact_type != ''")}
    facts.update(['进尺', '日历天', '人员出勤', '工况时间'])
    return facts


def _editor_options(conn) -> dict:
    leaves = _leaves(conn)
    return {
        'items': [{'item_code': c, 'item_name': i['item_name'], 'direction': i['direction']}
                  for c, i in leaves.items()],
        'facts': sorted(_facts(conn)),
        'pricing': PRICING,
        'combine': COMBINE,
        'granularity': GRANULARITY,
        'dimensions': DIMENSIONS,
        'operators': OPERATORS,
    }


def _apply_fields(conn, current: dict | None, body: dict, *, full: bool) -> dict:
    """合并并校验规则字段。full=True 用于手工新建（必填）；修改草稿时只校验提交了的字段。"""
    leaves = _leaves(conn)
    src = current or {}

    def pick(key, default=None):
        if key in body and body[key] is not None:
            return body[key]
        if key in src and src[key] is not None:
            return src[key]
        return default

    name = str(pick('name', '') or '').strip()
    if full or 'name' in body:
        if not name:
            raise HTTPException(422, '请填写规则名称')
        name = name[:60]
    elif not name:
        name = src.get('name')
    item_code = str(pick('item_code', '') or '').strip()
    if (full or 'item_code' in body) and item_code not in leaves:
        raise HTTPException(422, f'科目 {item_code or "（空）"} 不在科目树末级')
    method = str(pick('pricing_method', '') or '').strip()
    if (full or 'pricing_method' in body) and method not in PRICING:
        raise HTTPException(422, '计价方式不正确')
    if full and 'unit_price' not in body:
        raise HTTPException(422, '请填写单价')
    try:
        price = float(body['unit_price'] if 'unit_price' in body else src.get('unit_price'))
    except (TypeError, ValueError):
        raise HTTPException(422, '单价不是数字') from None
    if price < 0:
        raise HTTPException(422, '单价不能为负')
    unit = str(pick('unit', '') or '').strip()
    base = pick('base_item_code')
    base = str(base).strip() if base else None
    fact = pick('fact_type')
    fact = str(fact).strip() if fact else None
    if method == '比例':
        fact = None
        if (full or 'pricing_method' in body or 'base_item_code' in body) and (not base or base not in leaves):
            raise HTTPException(422, '比例类规则请选择基数科目')
        unit = unit or '元'
    else:
        base = None
        if (full or 'fact_type' in body or 'pricing_method' in body) and fact not in _facts(conn):
            raise HTTPException(422, f'触发事实“{fact or "（空）"}”不在系统字典')
        if (full or 'unit' in body) and not unit:
            raise HTTPException(422, '请填写计价单位')
    combine = str(pick('combine_mode', '互斥') or '互斥')
    if (full or 'combine_mode' in body) and combine not in COMBINE:
        raise HTTPException(422, '组合方式不正确')
    gran = str(pick('granularity', '逐日') or '逐日')
    if (full or 'granularity' in body) and gran not in GRANULARITY:
        raise HTTPException(422, '计算粒度不正确')
    vf = str(pick('valid_from', '') or '').strip()
    if (full or 'valid_from' in body) and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', vf):
        raise HTTPException(422, '请填写生效日期（YYYY-MM-DD）')
    direction = leaves[item_code]['direction'] if item_code in leaves else (src.get('direction') or '成本')
    order = 3 if method == '比例' else 1 if direction == '收入' else 2
    return {'name': name, 'item_code': item_code, 'fact_type': fact, 'pricing_method': method,
            'base_item_code': base, 'calc_order': order, 'unit_price': price, 'unit': unit,
            'combine_mode': combine, 'granularity': gran, 'valid_from': vf}


def _save_conditions(conn, rule_id: int, conditions: list[dict]) -> None:
    conn.execute('DELETE FROM rule_condition WHERE rule_id=?', (rule_id,))
    for c in conditions:
        if c.get('dimension') not in DIMENSIONS or c.get('operator') not in OPERATORS or not str(c.get('value') or '').strip():
            raise HTTPException(422, f"条件不合法：{c}")
        conn.execute('INSERT INTO rule_condition (rule_id, dimension, operator, value) VALUES (?,?,?,?)',
                     (rule_id, c['dimension'], c['operator'], str(c['value']).strip()))


def _manual_clause_ids(conn) -> dict[int, int]:
    """需人工计价条款 → 产生它的解析任务。写不成公式的条款已经挂在原依据文件上。"""
    found: dict[int, int] = {}
    for t in conn.execute("SELECT task_id, manual FROM ai_task WHERE manual IS NOT NULL AND manual != '[]'"):
        for m in jload(t['manual'], []):
            if m.get('clause_id'):
                found[int(m['clause_id'])] = t['task_id']
    return found


def _bind_clause(conn, body: dict) -> tuple[int, int | None]:
    """手工规则必须挂到条款上。返回 (clause_id, 若来自需人工计价则为其解析任务)。"""
    if body.get('clause_id'):
        try:
            clause_id = int(body['clause_id'])
        except (TypeError, ValueError):
            raise HTTPException(422, '条款不存在，不能生成没有依据的规则') from None
        row = conn.execute('SELECT clause_id FROM clause WHERE clause_id=?', (clause_id,)).fetchone()
        if not row:
            raise HTTPException(422, '条款不存在，不能生成没有依据的规则')
        return clause_id, _manual_clause_ids(conn).get(clause_id)
    if not body.get('doc_id'):
        raise HTTPException(422, '请选择依据文件并摘录条款。规则必须能追溯到原文，不能单独建立')
    try:
        doc_id = int(body['doc_id'])
    except (TypeError, ValueError):
        raise HTTPException(422, '依据文件不存在') from None
    doc = conn.execute('SELECT doc_id, full_text FROM source_doc WHERE doc_id=?', (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(422, '依据文件不存在')
    clause_no = str(body.get('clause_no') or '').strip()
    text = str(body.get('clause_text') or '').strip()
    page = str(body.get('page_no') or '').strip()
    if not clause_no:
        raise HTTPException(422, '请填写条款号')
    if len(re.sub(r'\s+', '', text)) < 4:
        raise HTTPException(422, '请摘录条款原文（至少 4 个字），以便日后追溯')
    full = doc['full_text'] or ''
    pre, hit, post, page_no = '', text, '', page
    if full:
        from .aiparse import _page_at, find_span
        span = find_span(full, text)
        if not span:
            raise HTTPException(422, '摘录的原文在该依据文件里找不到，请从文件中原样复制')
        a, b = span
        page_no = _page_at(full, a) or page_no
        pre = full[max(0, a - 80):a].split('\n')[-1]
        hit, post = full[a:b], full[b:b + 80].split('\n')[0]
    clause_id = conn.execute(
        'INSERT INTO clause (doc_id, clause_no, page_no, text_pre, text_hit, text_post) VALUES (?,?,?,?,?,?)',
        (doc_id, clause_no, page_no, pre, hit, post)).lastrowid
    return clause_id, None


def _new_code(conn) -> str:
    n = conn.execute('SELECT COALESCE(MAX(rule_id), 0) + 1 FROM economic_rule').fetchone()[0]
    while True:
        code = f'RL-M{n:04d}'
        if not conn.execute('SELECT 1 FROM economic_rule WHERE rule_code=?', (code,)).fetchone():
            return code
        n += 1


def form_options(conn) -> dict:
    """手工新建用：科目与字典，加上已上传的依据文件及其条款（含需人工计价）。"""
    from .aiparse import DOC_TYPES
    opt = _editor_options(conn)
    manual_ids = _manual_clause_ids(conn)
    docs = []
    for d in conn.execute('SELECT doc_id, doc_type, doc_no, doc_name, valid_from FROM source_doc ORDER BY doc_id DESC'):
        clauses = []
        for c in conn.execute('SELECT clause_id, clause_no, page_no, text_hit FROM clause WHERE doc_id=? ORDER BY clause_id',
                              (d['doc_id'],)):
            used = conn.execute("SELECT COUNT(*) n FROM economic_rule WHERE clause_id=? AND status!='已废止'",
                                (c['clause_id'],)).fetchone()['n']
            clauses.append({'clause_id': c['clause_id'], 'clause_no': c['clause_no'], 'page': c['page_no'] or '',
                            'text': (c['text_hit'] or '')[:300], 'manual': c['clause_id'] in manual_ids, 'rule_count': used})
        docs.append({**dict(d), 'clauses': clauses})
    opt['doc_types'] = DOC_TYPES
    opt['docs'] = docs
    return opt


def create(conn, user: dict, body: dict) -> dict:
    """普通账户手工新建草稿。不调用 AI，也不写入提交人；费用管理部室在提交前不能确认。"""
    if not _submitter(user):
        raise HTTPException(403, '只有普通账户可以新建规则草稿')
    clause_id, task_id = _bind_clause(conn, body)
    fields = _apply_fields(conn, None, body, full=True)
    params = {}
    if body.get('factor_rules'):
        params['factor_rules'] = body['factor_rules']
    code = _new_code(conn)
    rid = conn.execute(
        'INSERT INTO economic_rule (rule_code, version, name, item_code, clause_id, fact_type, pricing_method, base_item_code, '
        'calc_order, unit_price, unit, calc_params, combine_mode, granularity, priority, valid_from, status, origin, '
        "field_levels, ai_task_id) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,0,?, '草稿', '人工录入', '{}', ?)",
        (code, fields['name'], fields['item_code'], clause_id, fields['fact_type'], fields['pricing_method'],
         fields['base_item_code'], fields['calc_order'], fields['unit_price'], fields['unit'], jdump(params),
         fields['combine_mode'], fields['granularity'], fields['valid_from'], task_id)).lastrowid
    if body.get('conditions'):
        _save_conditions(conn, rid, body['conditions'])
    clause = conn.execute('SELECT c.clause_no, d.doc_name FROM clause c JOIN source_doc d ON d.doc_id=c.doc_id WHERE c.clause_id=?',
                          (clause_id,)).fetchone()
    note = f"{clause['doc_name']} {clause['clause_no']}"
    if task_id:
        note += '；由需人工计价条款补成草稿'
    log(conn, '规则', rid, '手工新建', user['who'], note)
    return detail(conn, user, rid)


def update(conn, user: dict, rule_id: int, body: dict) -> dict:
    r = _need_submitter(conn, user, rule_id)
    if r['status'] not in EDITABLE:
        raise HTTPException(409, '只有草稿可以修改；已提交的规则须由费用管理部室退回，已发布规则请新建版本')
    fields = _apply_fields(conn, r, body, full=False)
    params = jload(r['calc_params'])
    if 'factor_rules' in body:
        params['factor_rules'] = body['factor_rules']
    if body.get('pricing_method') and body['pricing_method'] != r['pricing_method']:
        params.pop('contract_mode', None)   # 计价方式改过之后，解析时写入的计价模式不再沿用
    conn.execute(
        'UPDATE economic_rule SET name=?, item_code=?, fact_type=?, pricing_method=?, base_item_code=?, calc_order=?, '
        'unit_price=?, unit=?, combine_mode=?, granularity=?, valid_from=?, calc_params=?, field_levels=? WHERE rule_id=?',
        (fields['name'], fields['item_code'], fields['fact_type'], fields['pricing_method'], fields['base_item_code'],
         fields['calc_order'], fields['unit_price'], fields['unit'], fields['combine_mode'], fields['granularity'],
         fields['valid_from'], jdump(params), jdump({k: '高' for k in jload(r['field_levels'])}), rule_id))
    if 'conditions' in body:
        _save_conditions(conn, rule_id, body['conditions'])
    log(conn, '规则', rule_id, '修改', user['who'], '人工修改 AI 草稿，修改前后差异已存为样本' if r['origin'] == 'AI提取' else None)
    return detail(conn, user, rule_id)


def _conflicts(conn, r: dict) -> list[str]:
    """自动检查：同科目、同条件、同时段已发布的互斥规则视为重叠。"""
    msgs = []
    mine = sorted((c['dimension'], c['operator'], c['value']) for c in engine.rule_conditions(conn, r['rule_id']))
    for o in conn.execute("SELECT * FROM economic_rule WHERE item_code=? AND status='已发布' AND rule_code!=? AND fact_type IS ?",
                          (r['item_code'], r['rule_code'], r['fact_type'])):
        theirs = sorted((c['dimension'], c['operator'], c['value']) for c in engine.rule_conditions(conn, o['rule_id']))
        if theirs == mine and o['combine_mode'] == '互斥' and r['combine_mode'] == '互斥' and o['priority'] == r['priority']:
            msgs.append(f"与已发布规则 {o['rule_code']} v{o['version']} 同科目、同条件、同优先级重叠")
    return msgs


def submit(conn, user: dict, rule_id: int) -> dict:
    """普通账户把草稿提交给归口部室。提交后价格和条件锁定，退回后才能再改。"""
    r = _need_submitter(conn, user, rule_id)
    if r['status'] != '草稿':
        raise HTTPException(409, f"当前状态“{r['status']}”不能提交")
    conn.execute("UPDATE economic_rule SET status='待确认', submitted_by=?, submitted_at=? WHERE rule_id=?",
                 (user['who'], now(), rule_id))
    log(conn, '规则', rule_id, '提交', user['who'], '提交归口部室确认')
    return detail(conn, user, rule_id)


def confirm(conn, user: dict, rule_id: int) -> dict:
    """归口部室确认本部室规则 → 冲突检查 → 回算；达标或豁免进入待发布，超阈值留在待确认。"""
    r = _need_reviewer(conn, user, rule_id)
    if r['status'] != '待确认':
        raise HTTPException(409, f"当前状态“{r['status']}”不能确认，须由普通账户先提交")
    conflicts = _conflicts(conn, r)
    if conflicts:
        raise HTTPException(409, '；'.join(conflicts))
    conn.execute("UPDATE economic_rule SET status='回算中', confirmed_by=?, confirmed_at=? WHERE rule_id=?",
                 (user['who'], now(), rule_id))
    log(conn, '规则', rule_id, '确认', user['who'])
    bt = engine.backtest(conn, rule_id)
    nxt = '待发布' if bt['result'] in ('通过', '豁免') else '待确认'
    conn.execute('UPDATE economic_rule SET status=? WHERE rule_id=?', (nxt, rule_id))
    log(conn, '规则', rule_id, '回算' + bt['result'], '系统', bt['message'])
    out = detail(conn, user, rule_id)
    out['backtest'] = bt
    return out


def reject(conn, user: dict, rule_id: int, comment: str | None) -> dict:
    """归口部室退回本部室规则：回到草稿，普通账户按原因修改后重新提交。"""
    r = _need_reviewer(conn, user, rule_id)
    if r['status'] not in ('待确认', '回算中', '待发布'):
        raise HTTPException(409, f"当前状态“{r['status']}”不能退回")
    comment = (comment or '').strip()
    if not comment:
        raise HTTPException(422, '退回必须填写原因')
    conn.execute("UPDATE economic_rule SET status='草稿' WHERE rule_id=?", (rule_id,))
    log(conn, '规则', rule_id, '退回', user['who'], comment)
    return detail(conn, user, rule_id)


def _publish(conn, user: dict, r: dict, change_basis: str) -> dict | None:
    """发布的实际动作（发布与影响预览共用）：旧版本失效日由新版本推导；生效日起已有事实按新规则冲销重算。"""
    rule_id = r['rule_id']
    prev = conn.execute("SELECT * FROM economic_rule WHERE rule_code=? AND status='已发布' AND rule_id!=?",
                        (r['rule_code'], rule_id)).fetchone()
    if prev:
        end = (date.fromisoformat(r['valid_from']) - timedelta(days=1)).isoformat()
        conn.execute("UPDATE economic_rule SET status='已被替代', valid_to=? WHERE rule_id=?", (end, prev['rule_id']))
        conn.execute('UPDATE economic_rule SET supersedes_rule_id=? WHERE rule_id=?', (prev['rule_id'], rule_id))
        log(conn, '规则', prev['rule_id'], '被替代', user['who'], f"由 v{r['version']} 替代，失效日 {end}")
    conn.execute("UPDATE economic_rule SET status='已发布', published_at=?, change_basis=? WHERE rule_id=?",
                 (now(), change_basis, rule_id))
    log(conn, '规则', rule_id, '发布', user['who'], f"生效日 {r['valid_from']}；变化依据：{change_basis}")
    wells, periods = engine.affected_scope(conn)
    periods = [p for p in periods if p >= r['valid_from'][:7]]
    return engine.recalc(conn, wells, periods, '规则新版本发布', user['who']) if periods else None


def _ready(conn, user: dict, rule_id: int) -> dict:
    r = _need_publisher(conn, user, rule_id)
    if r['status'] != '待发布':
        raise HTTPException(409, '只有费用管理部室确认且回算达标（或豁免）的规则可以发布')
    return r


def publish(conn, user: dict, rule_id: int, change_basis: str) -> dict:
    r = _ready(conn, user, rule_id)
    change_basis = (change_basis or '').strip()
    if len(change_basis) < 4:
        raise HTTPException(422, '请登记变化依据：部室提供的文件、通知或会议纪要（文号与名称）')
    batch = _publish(conn, user, r, change_basis)
    out = detail(conn, user, rule_id)
    out['batch'] = batch
    return out


def impact(conn, user: dict, rule_id: int) -> dict:
    """影响预览：在保存点里按发布试算，统计将新增 / 冲销的事项和各井金额变化，再回滚到保存点，数据库不留任何痕迹。"""
    r = _ready(conn, user, rule_id)
    conn.execute('SAVEPOINT rule_impact')
    try:
        batch = _publish(conn, user, r, '（影响预览）')
        wells = []
        if batch:
            wells = [dict(x) for x in conn.execute(
                'SELECT well_id, period, COUNT(*) n, SUM(amount) delta FROM economic_event WHERE batch_id=? '
                'GROUP BY well_id, period HAVING ABS(SUM(amount))>=0.5 ORDER BY ABS(SUM(amount)) DESC', (batch['batch_id'],))]
    finally:
        conn.execute('ROLLBACK TO rule_impact')
        conn.execute('RELEASE rule_impact')
    return {'created': batch['created'] if batch else 0, 'reversed': batch['reversed'] if batch else 0,
            'amount_change': batch['amount_change'] if batch else 0,
            'wells': [{**w, 'delta': money(w['delta'])} for w in wells[:50]], 'well_count': len(wells)}


def rollback(conn, user: dict, rule_id: int, reason: str | None) -> dict:
    """发布后发现有误时的补救：本版本废止；有上一版本则恢复它生效（失效日清空），没有则规则停用；受影响月份冲销重算。"""
    r = _need_publisher(conn, user, rule_id)
    if r['status'] != '已发布':
        raise HTTPException(409, '只有已发布的规则可以回滚')
    reason = (reason or '').strip()
    if not reason:
        raise HTTPException(422, '请填写回滚原因')
    conn.execute("UPDATE economic_rule SET status='已废止' WHERE rule_id=?", (rule_id,))
    prev = conn.execute('SELECT * FROM economic_rule WHERE rule_id=?', (r['supersedes_rule_id'],)).fetchone() \
        if r['supersedes_rule_id'] else None
    if prev:
        conn.execute("UPDATE economic_rule SET status='已发布', valid_to=NULL WHERE rule_id=?", (prev['rule_id'],))
        log(conn, '规则', prev['rule_id'], '恢复生效', user['who'], f"v{r['version']} 回滚：{reason}")
    log(conn, '规则', rule_id, '回滚', user['who'], reason + (f"；恢复 v{prev['version']}" if prev else '；无上一版本，规则停用'))
    since = min(r['valid_from'], prev['valid_from']) if prev else r['valid_from']
    wells, periods = engine.affected_scope(conn)
    periods = [p for p in periods if p >= since[:7]]
    batch = engine.recalc(conn, wells, periods, '规则回滚', user['who']) if periods else None
    out = detail(conn, user, prev['rule_id'] if prev else rule_id)
    out['batch'] = batch
    out['restored'] = prev['version'] if prev else None
    return out


def new_version(conn, user: dict, rule_id: int, valid_from: str | None) -> dict:
    r = _need_submitter(conn, user, rule_id)
    if r['status'] != '已发布':
        raise HTTPException(409, '只有已发布的规则可以新建版本')
    pending = conn.execute("SELECT rule_id FROM economic_rule WHERE rule_code=? AND status IN ('草稿','待确认','回算中','待发布')",
                           (r['rule_code'],)).fetchone()
    if pending:
        return detail(conn, user, pending['rule_id'])
    v = conn.execute('SELECT MAX(version) v FROM economic_rule WHERE rule_code=?', (r['rule_code'],)).fetchone()['v'] + 1
    nid = conn.execute(
        'INSERT INTO economic_rule (rule_code, version, name, item_code, clause_id, fact_type, pricing_method, base_item_code, '
        'calc_order, unit_price, unit, calc_params, combine_mode, granularity, priority, valid_from, status, origin, field_levels) '
        "SELECT rule_code, ?, name, item_code, clause_id, fact_type, pricing_method, base_item_code, calc_order, unit_price, unit, "
        "calc_params, combine_mode, granularity, priority, ?, '草稿', '人工录入', '{}' FROM economic_rule WHERE rule_id=?",
        (v, valid_from or date.today().isoformat(), rule_id)).lastrowid
    for c in engine.rule_conditions(conn, rule_id):
        conn.execute('INSERT INTO rule_condition (rule_id, dimension, operator, value) VALUES (?,?,?,?)',
                     (nid, c['dimension'], c['operator'], c['value']))
    log(conn, '规则', nid, '新建版本', user['who'], f"复制自 v{r['version']}")
    return detail(conn, user, nid)
