"""归口部室：短标记范围、登录名、变化依据。

账号按费用科目上的短标记分开，不用部室全称，也不再把物资和装备、企划和财务并成一个账号。
`市场/安全`、`各部室` 不是独立部室：末级科目按名称后缀归入对应短标记，组节点按斜杠拆开后共享。
"""
import re

from fastapi import HTTPException

from .db import log, money, now

# 登录名 → 费用科目 owner_dept 短标记。显示名只在和短标记不同时另写。
ACCOUNTS = (
    ('shichang', '市场'),
    ('gongcheng', '技术'),
    ('wuzi', '物资'),
    ('zhuangbei', '装备'),
    ('anquan', '安全'),
    ('renli', '人事'),
    ('qihua', '企划'),
    ('caiwu', '财务'),
)
# 界面上的叫法。过滤仍用短标记，所以工程看的是“技术”，人力看的是“人事”。
SCOPE_NAME = {
    '市场': '市场', '技术': '工程', '物资': '物资', '装备': '装备',
    '安全': '安全', '人事': '人力', '财务': '财务', '企划': '企划',
}
DEPT_SCOPES = tuple(SCOPE_NAME)
# 旧的全称登录名 → 现登录名。物资装备部、企划财务部是拆开的，不在这里改名。
RENAMES = {
    'shichangbu': 'shichang',
    'gongchengjishubu': 'gongcheng',
    'anquanhuanbaobu': 'anquan',
    'renliziyuanbu': 'renli',
}
# 升级时删掉的旧登录名，避免和短名并存。
RETIRED_USERNAMES = (
    'shichangbu', 'gongchengjishubu', 'wuzizhuangbeibu', 'anquanhuanbaobu',
    'renliziyuanbu', 'qihuacaiwubu', 'feiyong', 'feiyongguanlibushi',
)
# 库里偶尔写成全称。物资装备部不回写成一个标记，避免物资和装备又看同一批科目。
_FULL_TO_SHORT = {
    '市场部': '市场', '工程技术部': '技术', '安全环保部': '安全',
    '人力资源部': '人事', '企划财务部': '财务',
}
_SPLIT = re.compile(r'[/／]')
_SUFFIX = re.compile(r'[（(]([^）)]+)[）)]\s*$')


def scope_name(scope: str) -> str:
    return SCOPE_NAME.get(scope, scope)


def _one(label: str) -> str | None:
    """单个标记收成短标记。斜杠和“各部室”不是一个部室。"""
    label = (label or '').strip()
    if not label or label == '各部室' or _SPLIT.search(label):
        return None
    if label in SCOPE_NAME:
        return label
    if label in _FULL_TO_SHORT:
        return _FULL_TO_SHORT[label]
    return label


def departments_for_item(owner: str | None, name: str | None = None) -> set[str]:
    """这条费用科目属于哪些短标记。空集表示不单独归属。"""
    raw = (owner or '').strip()
    if not raw:
        return set()
    single = _one(raw)
    if single:
        return {single}
    if raw == '各部室' or _SPLIT.search(raw):
        matched = _SUFFIX.search(name or '')
        if matched:
            hit = _one(matched.group(1))
            if hit:
                return {hit}
        if _SPLIT.search(raw):
            return {hit for part in _SPLIT.split(raw) if (hit := _one(part))}
    return set()


def covers(dept: str | None, owner: str | None, name: str | None = None) -> bool:
    return bool(dept) and dept in departments_for_item(owner, name)


def distinct_departments(conn) -> list[str]:
    """新建部室账号时可选的短标记。conn 保留给调用方，名单不随库里的科目增减。"""
    del conn
    return list(DEPT_SCOPES)


def scoped_item_rows(rows: list[dict], dept: str) -> list[dict]:
    """本部室科目，加上显示树需要的上级节点。不带入其他部室的末级科目。"""
    by = {r['item_code']: r for r in rows}
    keep: set[str] = set()
    for row in rows:
        if not covers(dept, row.get('owner_dept'), row.get('item_name')):
            continue
        code = row['item_code']
        while code and code not in keep:
            keep.add(code)
            code = by.get(code, {}).get('parent_code')
    return [row for row in rows if row['item_code'] in keep]


def dept_results(conn, user: dict) -> dict:
    """本部室末级科目的已入账金额。只汇总经济事项里已有的数，不调用规则引擎。"""
    if user['role'] != 'dept':
        raise HTTPException(403, '当前角色无此功能权限')
    dept = user.get('dept') or ''
    items = [dict(r) for r in conn.execute(
        'SELECT item_code, item_name, owner_dept, is_leaf FROM cost_item ORDER BY sort_no')]
    matched = [it for it in items if it['is_leaf'] and covers(dept, it['owner_dept'], it['item_name'])]
    codes = [it['item_code'] for it in matched]
    amounts: dict[str, int] = {}
    if codes:
        marks = ','.join('?' * len(codes))
        for row in conn.execute(
                f'SELECT item_code, COALESCE(SUM(amount), 0) a FROM economic_event WHERE item_code IN ({marks}) GROUP BY item_code',
                codes):
            amounts[row['item_code']] = money(row['a'])
    return {
        'dept': dept,
        'items': [{
            'item_code': it['item_code'], 'item_name': it['item_name'], 'owner_dept': it['owner_dept'],
            'amount': amounts.get(it['item_code'], 0),
        } for it in matched],
    }


def list_basis(conn, user: dict) -> list[dict]:
    """部室只看自己提交的变化依据；管理端和系统管理员看全部，供发布规则时引用。"""
    sql = 'SELECT basis_id, dept, title, note, submitted_by, submitted_at FROM dept_change_basis'
    if user['role'] == 'dept':
        rows = conn.execute(sql + ' WHERE dept=? ORDER BY basis_id DESC', (user.get('dept'),)).fetchall()
    elif user['role'] in ('finance', 'admin'):
        rows = conn.execute(sql + ' ORDER BY basis_id DESC').fetchall()
    else:
        raise HTTPException(403, '当前角色无此功能权限')
    return [dict(r) for r in rows]


def submit_basis(conn, user: dict, title: str, note: str | None) -> dict:
    """归口部室提交变化依据。不写规则、不改金额。部室由登录账号决定，不能代其他部室提交。"""
    if user['role'] != 'dept':
        raise HTTPException(403, '只有归口部室可以提交变化依据')
    dept = (user.get('dept') or '').strip()
    if not dept:
        raise HTTPException(403, '账号未绑定归口部室')
    title = (title or '').strip()
    if len(title) < 4:
        raise HTTPException(422, '请填写变化依据：文件、通知或会议纪要的文号与名称')
    if len(title) > 200:
        raise HTTPException(422, '变化依据过长')
    note = (note or '').strip() or None
    if note and len(note) > 1000:
        raise HTTPException(422, '补充说明过长')
    basis_id = conn.execute(
        'INSERT INTO dept_change_basis (dept, title, note, submitted_by, submitted_at) VALUES (?,?,?,?,?)',
        (dept, title, note, user['who'], now())).lastrowid
    log(conn, '变化依据', basis_id, '提交', user['who'], title)
    row = conn.execute(
        'SELECT basis_id, dept, title, note, submitted_by, submitted_at FROM dept_change_basis WHERE basis_id=?',
        (basis_id,)).fetchone()
    return dict(row)
