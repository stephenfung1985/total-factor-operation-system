"""主数据维护：队伍、物资、计量单位、日报报量项目、费用科目说明字段。

每次修改写一条 approval_log（对象类型“主数据”），备注列出“字段 旧值 → 新值”。
费用科目只开放说明字段：编码、层级、方向、成本性态关系到计算口径和导出模板，不在这里改。
"""
from fastapi import HTTPException

from .db import log

KIND_NAMES = {'teams': '队伍', 'materials': '物资', 'units': '计量单位', 'report_lines': '报量项目', 'items': '费用科目'}
FIELD_NAMES = {
    'name': '名称', 'rig_type': '钻机型号', 'unit': '计量单位', 'captain': '队长', 'item_code': '费用科目', 'enabled': '启用',
    'usage': '用于', 'label': '名称', 'needs_doc': '须填单据', 'sort_no': '排序',
    'owner_dept': '归口部室', 'calc_type': '计价类型', 'report_col': '总表列', 'finance_account': '会计科目',
}
TEAM_FIELDS = ('name', 'rig_type', 'unit', 'captain')
MATERIAL_FIELDS = ('name', 'unit', 'item_code', 'enabled')
LINE_FIELDS = ('label', 'unit', 'enabled', 'needs_doc', 'sort_no')
ITEM_FIELDS = ('owner_dept', 'calc_type', 'report_col', 'finance_account')


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    if isinstance(v, bool):
        return int(v)
    return v


def _show(k: str, v) -> str:
    if k in ('enabled', 'needs_doc'):
        return '是' if v else '否'
    return '空' if v is None or v == '' else str(v)


def _update(conn, table: str, key_col: str, key, old: dict, changes: dict, kind: str, operator: str) -> dict:
    diff = {k: v for k, v in changes.items() if old.get(k) != v}
    if diff:
        conn.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in diff)} WHERE {key_col}=?", (*diff.values(), key))
        log(conn, '主数据', f'{KIND_NAMES[kind]}:{key}', '修改', operator,
            '；'.join(f'{FIELD_NAMES[k]} {_show(k, old.get(k))} → {_show(k, v)}' for k, v in diff.items()))
    return {**old, **diff, 'changed': list(diff)}


def _pick(body: dict, fields: tuple) -> dict:
    return {k: _clean(body[k]) for k in fields if k in body}


def _require(changes: dict, *keys: str) -> None:
    for k in keys:
        if k in changes and changes[k] is None:
            raise HTTPException(422, f'{FIELD_NAMES[k]}不能为空')


def _leaf_item(conn, code: str | None) -> None:
    if code is None:
        return
    if not conn.execute('SELECT 1 FROM cost_item WHERE item_code=? AND is_leaf=1', (code,)).fetchone():
        raise HTTPException(422, f'费用科目 {code} 不存在或不是末级科目')


def _one(conn, sql: str, key, what: str) -> dict:
    r = conn.execute(sql, (key,)).fetchone()
    if not r:
        raise HTTPException(404, f'{what} {key} 不存在')
    return dict(r)


# ---------- 队伍 ----------

def update_team(conn, team_id: str, body: dict, operator: str) -> dict:
    old = _one(conn, 'SELECT team_id, team_no, name, rig_type, unit, captain FROM team WHERE team_id=?', team_id, '队伍')
    changes = _pick(body, TEAM_FIELDS)
    _require(changes, 'name')
    out = _update(conn, 'team', 'team_id', team_id, old, changes, 'teams', operator)
    if 'name' in out['changed']:   # 账号上的身份说明、数据范围按队名快照，随队名一起改
        conn.execute('UPDATE app_user SET scope_label=?, who=REPLACE(who, ?, ?) WHERE team_id=?',
                     (out['name'], old['name'], out['name'], team_id))
    return out


# ---------- 物资 ----------

def create_material(conn, body: dict, operator: str) -> dict:
    code = _clean(body.get('material_code'))
    f = _pick(body, ('name', 'unit', 'item_code'))
    if not code or not f.get('name') or not f.get('unit'):
        raise HTTPException(422, '物资编码、名称、计量单位必填')
    if conn.execute('SELECT 1 FROM material WHERE material_code=?', (code,)).fetchone():
        raise HTTPException(409, f'物资编码 {code} 已存在')
    _leaf_item(conn, f.get('item_code'))
    conn.execute('INSERT INTO material (material_code, name, unit, item_code, enabled) VALUES (?,?,?,?,1)',
                 (code, f['name'], f['unit'], f.get('item_code')))
    log(conn, '主数据', f'物资:{code}', '新增', operator, f"{f['name']} · {f['unit']} · {f.get('item_code') or '未关联科目'}")
    return {'material_code': code, **f, 'enabled': 1}


def update_material(conn, code: str, body: dict, operator: str) -> dict:
    old = _one(conn, 'SELECT material_code, name, unit, item_code, enabled FROM material WHERE material_code=?', code, '物资')
    changes = _pick(body, MATERIAL_FIELDS)
    _require(changes, 'name', 'unit')
    _leaf_item(conn, changes.get('item_code'))
    return _update(conn, 'material', 'material_code', code, old, changes, 'materials', operator)


# ---------- 计量单位 ----------
# 单位名称被物资、报量项目、规则按文字引用，只能新增和改“用于”说明，不改名。

def create_unit(conn, body: dict, operator: str) -> dict:
    unit, usage = _clean(body.get('unit')), _clean(body.get('usage'))
    if not unit or not usage:
        raise HTTPException(422, '计量单位、用于必填')
    if conn.execute('SELECT 1 FROM unit_of_measure WHERE unit=?', (unit,)).fetchone():
        raise HTTPException(409, f'计量单位 {unit} 已存在')
    conn.execute('INSERT INTO unit_of_measure (unit, usage) VALUES (?,?)', (unit, usage))
    log(conn, '主数据', f'计量单位:{unit}', '新增', operator, usage)
    return {'unit': unit, 'usage': usage}


def update_unit(conn, unit: str, body: dict, operator: str) -> dict:
    old = _one(conn, 'SELECT unit, usage FROM unit_of_measure WHERE unit=?', unit, '计量单位')
    changes = _pick(body, ('usage',))
    _require(changes, 'usage')
    return _update(conn, 'unit_of_measure', 'unit', unit, old, changes, 'units', operator)


# ---------- 日报报量项目 ----------

def line_in_use(conn, key: str) -> bool:
    """报量项目是否已产生业务事实：日报里填过、或已生成事实，或有规则按它取量。"""
    if conn.execute("SELECT 1 FROM business_fact WHERE json_extract(attributes, '$.line')=? LIMIT 1", (key,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM daily_report WHERE json_type(lines, '$.\"' || ? || '\"') IS NOT NULL LIMIT 1", (key,)).fetchone():
        return True
    return bool(conn.execute("SELECT 1 FROM economic_rule WHERE json_extract(calc_params, '$.line')=? LIMIT 1", (key,)).fetchone())


def update_line(conn, key: str, body: dict, operator: str) -> dict:
    old = _one(conn, 'SELECT line_key, label, unit, enabled, needs_doc, sort_no FROM report_line WHERE line_key=?', key, '报量项目')
    changes = _pick(body, LINE_FIELDS)
    _require(changes, 'label', 'unit', 'sort_no')
    if 'sort_no' in changes:
        changes['sort_no'] = int(changes['sort_no'])
    return _update(conn, 'report_line', 'line_key', key, old, changes, 'report_lines', operator)


def delete_line(conn, key: str, operator: str) -> dict:
    old = _one(conn, 'SELECT line_key, label FROM report_line WHERE line_key=?', key, '报量项目')
    if line_in_use(conn, key):
        raise HTTPException(409, f'报量项目 {old["label"]} 已有日报或业务事实，不能删除，只能停用')
    conn.execute('DELETE FROM report_line WHERE line_key=?', (key,))
    log(conn, '主数据', f'报量项目:{key}', '删除', operator, old['label'])
    return {'line_key': key, 'deleted': True}


# ---------- 费用科目（只开放说明字段） ----------

def update_item(conn, code: str, body: dict, operator: str) -> dict:
    locked = [k for k in body if k not in ITEM_FIELDS]
    if locked:
        raise HTTPException(422, f'费用科目只能修改归口部室、计价类型、总表列、会计科目；不能修改：{"、".join(locked)}')
    old = _one(conn, 'SELECT item_code, item_name, owner_dept, calc_type, report_col, finance_account FROM cost_item WHERE item_code=?',
               code, '费用科目')
    return _update(conn, 'cost_item', 'item_code', code, old, _pick(body, ITEM_FIELDS), 'items', operator)
