"""归口部室：短名对照、全拼登录名、行级范围、变化依据。

费用科目 `owner_dept` 多用短标记（市场、技术、物资、装备……）。物资和装备是同一个部室。
`市场/安全`、`各部室` 不是独立部室：末级科目按名称后缀（如“水费（市场）”）归到对应部室，组节点按斜杠拆开的短名共享，不另建账号。
"""
import re

from fastapi import HTTPException

from .db import log, money, now

# 汉语拼音全拼只保留字母数字，避免声调、分隔符进入登录名。
_PINYIN_OK = re.compile(r'^[a-z0-9]{2,32}$')
_SPLIT = re.compile(r'[/／]')
_SUFFIX = re.compile(r'[（(]([^）)]+)[）)]\s*$')


def name_map() -> dict[str, str]:
    """短标记 → 部室全称。定义在 seed.DEPT_FULL，账号和过滤共用这一份。"""
    from .seed import DEPT_FULL
    return DEPT_FULL


def quanpin(text: str) -> str:
    """汉语拼音全拼：小写、无声调、无分隔。同一字符串的结果稳定。"""
    from pypinyin import Style, lazy_pinyin
    parts = lazy_pinyin(text or '', style=Style.NORMAL, errors='ignore')
    return re.sub(r'[^a-z0-9]', '', ''.join(parts).lower())


def _one(label: str) -> str | None:
    """单个标记变成部室全称。斜杠和“各部室”不是一个部室。未知的单一名称原样保留，避免漏建账号。"""
    label = (label or '').strip()
    if not label or label == '各部室' or _SPLIT.search(label):
        return None
    mapping = name_map()
    if label in mapping:
        return mapping[label]
    if label in set(mapping.values()):
        return label
    return label


def departments_for_item(owner: str | None, name: str | None = None) -> set[str]:
    """这条费用科目属于哪些部室全称。空集表示不单独归属（共享组节点、或没有归口）。"""
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
    """库里实际出现的归口部室全称。已知部室按对照表顺序，其余按全拼排序。"""
    found: set[str] = set()
    for row in conn.execute('SELECT owner_dept, item_name FROM cost_item'):
        found |= departments_for_item(row['owner_dept'], row['item_name'])
    ordered: list[str] = []
    seen: set[str] = set()
    for full in dict.fromkeys(name_map().values()):
        if full in found:
            ordered.append(full)
            seen.add(full)
    extras = sorted((n for n in found if n not in seen), key=lambda n: (quanpin(n), n))
    return ordered + extras


def department_accounts(conn) -> list[tuple[str, str]]:
    """(登录名, 部室全称)。一个部室一个登录名；全拼相撞时拒绝启动，避免静默丢掉一个部室。"""
    out: list[tuple[str, str]] = []
    taken: dict[str, str] = {}
    for name in distinct_departments(conn):
        username = quanpin(name)
        if not _PINYIN_OK.fullmatch(username):
            raise RuntimeError(f'归口部室“{name}”的全拼登录名“{username}”不合法')
        if username in taken:
            raise RuntimeError(f'归口部室“{name}”与“{taken[username]}”的全拼登录名相同（{username}）')
        taken[username] = name
        out.append((username, name))
    return out


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
