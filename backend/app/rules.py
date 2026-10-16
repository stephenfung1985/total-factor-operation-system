"""规则维护：草稿 → 待确认 → 回算中 → 待发布 → 已发布 → 已被替代 / 已废止；任一步驳回退回草稿。

专业部室不再审核规则：部室只提供变化依据（合同、补充协议、制度、通知），由管理端录入、确认并发布。
没有了第二道人工审核，靠下面几道系统关口兜底：
  确认时自动检查冲突并用历史井回算，超阈值退回待确认；
  发布前可做影响预览（按发布试算后整体回滚，不落库），看清各井金额变化再发；
  发布必须登记变化依据，写入规则与操作日志，可追溯；
  发布后发现有误可一键回滚：本版本废止、上一版本恢复生效，受影响月份冲销重算。
已发布规则只增不改：调价时复制为新版本草稿，发布后旧版本失效日由新版本推导。
"""
from datetime import date, timedelta

from fastapi import HTTPException

from . import engine
from .db import jdump, jload, log, money, now

STEPS = ['草稿', '待确认', '回算中', '待发布', '已发布']
EDITABLE = ('草稿', '待确认')
DIMENSIONS = ['市场', '区块', '甲方', '井别', '井型', '井深', '开次', '队伍', '钻机型号']
OPERATORS = ['等于', '属于', '大于', '小于']


def _row(conn, rule_id: int) -> dict:
    r = conn.execute('SELECT r.*, i.item_name, i.owner_dept FROM economic_rule r JOIN cost_item i ON i.item_code=r.item_code '
                     'WHERE r.rule_id=?', (rule_id,)).fetchone()
    if not r:
        raise HTTPException(404, '规则不存在')
    return dict(r)


def _visible(user: dict, r: dict) -> bool:
    return user['role'] == 'finance'


def _can_edit(user: dict, r: dict) -> bool:
    return user['role'] == 'finance'


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
    fields = [('科目', f"{r['item_code']} {r['item_name']}"), ('触发事实', r['fact_type'] or f"比例基数 {r['base_item_code']}"),
              ('计价方式', r['pricing_method']), ('单价', price), ('适用条件', cond_text or '无（全部适用）'),
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
                                   'pricing_method', 'fact_type', 'owner_dept', 'priority')},
        'params': params, 'conditions': conds,
        'fields': [{'label': a, 'value': b, 'level': levels.get(a, '高')} for a, b in fields],
        'clause': clause, 'tests': tests, 'versions': versions, 'logs': logs,
        'hits': {'count': hits['n'], 'amount': money(hits['a'])},
        'can_edit': _can_edit(user, r) and r['status'] in EDITABLE,
        'can_confirm': _can_edit(user, r) and r['status'] in EDITABLE,
        'can_publish': _can_edit(user, r) and r['status'] == '待发布',
        'can_reject': _can_edit(user, r) and r['status'] in ('待确认', '回算中', '待发布'),
        'can_new_version': _can_edit(user, r) and r['status'] == '已发布',
        'can_rollback': _can_edit(user, r) and r['status'] == '已发布',
        'change_basis': r.get('change_basis'),
        'owner_dept': r['owner_dept'],
        'options': {'dimensions': DIMENSIONS, 'operators': OPERATORS},
    }


def _need_edit(conn, user, rule_id) -> dict:
    r = _row(conn, rule_id)
    if not _can_edit(user, r):
        raise HTTPException(403, '规则由管理端维护，当前角色只读')
    return r


def update(conn, user: dict, rule_id: int, body: dict) -> dict:
    r = _need_edit(conn, user, rule_id)
    if r['status'] not in EDITABLE:
        raise HTTPException(409, '只有草稿和待确认的规则可以修改；已发布规则请新建版本')
    params = jload(r['calc_params'])
    if 'factor_rules' in body:
        params['factor_rules'] = body['factor_rules']
    price = float(body.get('unit_price', r['unit_price']))
    if price < 0:
        raise HTTPException(422, '单价不能为负')
    conn.execute('UPDATE economic_rule SET unit_price=?, combine_mode=?, granularity=?, valid_from=?, calc_params=?, '
                 'field_levels=? WHERE rule_id=?',
                 (price, body.get('combine_mode', r['combine_mode']), body.get('granularity', r['granularity']),
                  body.get('valid_from', r['valid_from']), jdump(params),
                  jdump({k: '高' for k in jload(r['field_levels'])}), rule_id))
    if 'conditions' in body:
        conn.execute('DELETE FROM rule_condition WHERE rule_id=?', (rule_id,))
        for c in body['conditions']:
            if c['dimension'] not in DIMENSIONS or c['operator'] not in OPERATORS or not str(c['value']).strip():
                raise HTTPException(422, f"条件不合法：{c}")
            conn.execute('INSERT INTO rule_condition (rule_id, dimension, operator, value) VALUES (?,?,?,?)',
                         (rule_id, c['dimension'], c['operator'], str(c['value']).strip()))
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


def confirm(conn, user: dict, rule_id: int) -> dict:
    """人工确认 → 自动检查 → 回算；达标或豁免进入待发布，超阈值退回待确认。"""
    r = _need_edit(conn, user, rule_id)
    if r['status'] not in EDITABLE:
        raise HTTPException(409, f"当前状态“{r['status']}”不能确认")
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
    r = _need_edit(conn, user, rule_id)
    if r['status'] not in ('待确认', '回算中', '待发布'):
        raise HTTPException(409, f"当前状态“{r['status']}”不能驳回")
    conn.execute("UPDATE economic_rule SET status='草稿' WHERE rule_id=?", (rule_id,))
    log(conn, '规则', rule_id, '驳回', user['who'], comment)
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
    r = _need_edit(conn, user, rule_id)
    if r['status'] != '待发布':
        raise HTTPException(409, '只有回算达标（或豁免）的规则可以发布')
    return r


def publish(conn, user: dict, rule_id: int, change_basis: str) -> dict:
    change_basis = (change_basis or '').strip()
    if len(change_basis) < 4:
        raise HTTPException(422, '请登记变化依据：部室提供的文件、通知或会议纪要（文号与名称）')
    r = _ready(conn, user, rule_id)
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
    reason = (reason or '').strip()
    if not reason:
        raise HTTPException(422, '请填写回滚原因')
    r = _need_edit(conn, user, rule_id)
    if r['status'] != '已发布':
        raise HTTPException(409, '只有已发布的规则可以回滚')
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
    r = _need_edit(conn, user, rule_id)
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
