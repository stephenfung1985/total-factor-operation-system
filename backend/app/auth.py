"""角色、菜单与两级权限。

功能权限：能做什么（编辑 / 审核 / 查看），决定菜单和按钮是否出现。
数据权限：能看哪些行。基层按队伍过滤，部室按费用项目归口部室过滤，下钻每一层都生效。

登录后以 Authorization: Bearer <令牌> 识别账号（见 accounts）。
自动化测试可设置环境变量 TFOS_DEMO_AUTH=1，用请求头 X-User-Id 直接指定账号；正式运行不开启。
接入统一身份认证时只需替换 current_user。
"""
import os

from fastapi import Header, HTTPException

from . import accounts
from .db import connect

NAV = {
    'finance': [
        {'label': '经营', 'items': [{'id': 'overview', 'label': '经营总览'}, {'id': 'benchmark', 'label': '对标分析'},
                                  {'id': 'costdetail', 'label': '费用导出'}]},
        {'label': '填报', 'items': [{'id': 'wellreg', 'label': '基础信息填报'}, {'id': 'report', 'label': '成本日报'}]},
        {'label': '规则', 'items': [{'id': 'rules', 'label': '规则确认'}, {'id': 'review', 'label': '日报审核'},
                                  {'id': 'masterdata', 'label': '主数据维护'}]},
        {'label': '系统', 'items': [{'id': 'perm', 'label': '角色与权限'}, {'id': 'batches', 'label': '历史记录'}]},
    ],
    'team': [
        {'label': '填报', 'items': [{'id': 'wellreg', 'label': '基础信息'}, {'id': 'report', 'label': '成本日报'}]},
        {'label': '经营', 'items': [{'id': 'overview', 'label': '本队单井收支'}]},
    ],
    'dept': [
        {'label': '规则', 'items': [{'id': 'rules', 'label': '规则确认'}, {'id': 'review', 'label': '日报审核'}]},
        {'label': '经营', 'items': [{'id': 'overview', 'label': '归口科目分析'}, {'id': 'costdetail', 'label': '费用导出'}]},
    ],
}

ROLE_NAMES = {'finance': '管理层', 'team': '基层钻井队', 'dept': '专业部室'}

# 功能 → 各角色权限（team, dept, finance）
PERMS = [
    ('成本日报填报（管理层须先选队伍）', ['编辑', '', '编辑']),
    ('成本日报审核', ['', '审核', '审核']),
    ('依据文件与规则草稿', ['', '编辑', '编辑']),
    ('规则发布审批', ['', '审核', '审核']),
    ('基础信息填报（含人员数量，人工费按此核算；管理层须先选队伍）', ['编辑', '', '编辑']),
    ('单井基础信息汇总、导入导出、退回修改', ['', '', '编辑']),
    ('费用科目树与主数据', ['查看', '查看', '编辑']),
    ('计算批次与手工重算', ['', '', '编辑']),
    ('经营报表与下钻', ['查看', '查看', '查看']),
    ('对标分析', ['', '', '查看']),
    ('费用导出（按时间、市场、区块、队伍）', ['', '导出', '导出']),
    ('单井经营结果模板导入', ['', '', '编辑']),
    ('角色与权限', ['', '', '编辑']),
]


def _acting(conn, u: dict, team_id: str | None) -> dict:
    """管理层代队伍填报：请求头 X-Team-Id 指定队伍，仅对本次请求生效。"""
    if u['role'] == 'finance' and team_id:
        if not conn.execute('SELECT 1 FROM team WHERE team_id=?', (team_id,)).fetchone():
            raise HTTPException(400, '队伍不存在')
        return {**u, 'team_id': team_id}
    return u


def need_team(user: dict) -> str:
    if not user.get('team_id'):
        raise HTTPException(400, '请先选择要填报的队伍')
    return user['team_id']


def current_user(authorization: str | None = Header(default=None), x_user_id: str | None = Header(default=None),
                 x_team_id: str | None = Header(default=None)) -> dict:
    conn = connect()
    try:
        if authorization and authorization.startswith('Bearer '):
            u = accounts.user_by_token(conn, authorization[7:].strip())
            if not u:
                raise HTTPException(401, '登录已过期，请重新登录')
            return _acting(conn, u, x_team_id)
        if os.environ.get('TFOS_DEMO_AUTH') == '1' and x_user_id:
            r = conn.execute('SELECT * FROM app_user WHERE user_id=?', (x_user_id,)).fetchone()
            if r:
                return _acting(conn, dict(r), x_team_id)
        raise HTTPException(401, '请先登录')
    finally:
        conn.close()


def require(user: dict, *roles: str) -> None:
    # 管理层包含基层钻井队和专业部室的全部功能权限
    if user['role'] != 'finance' and user['role'] not in roles:
        raise HTTPException(403, '当前角色无此功能权限')


def require_team(user: dict) -> None:
    """基层队功能：基层队本人，或已选定队伍的管理层。"""
    require(user, 'team')
    need_team(user)


def visible_wells(conn, user: dict) -> list[str]:
    if user['role'] == 'team' or (user['role'] == 'finance' and user.get('team_id')):
        return [r['well_id'] for r in conn.execute(
            'SELECT DISTINCT well_id FROM team_well_period WHERE team_id=? ORDER BY well_id', (user['team_id'],))]
    return [r['well_id'] for r in conn.execute('SELECT well_id FROM well ORDER BY well_id')]


def check_well(conn, user: dict, well_id: str) -> None:
    if well_id not in visible_wells(conn, user):
        raise HTTPException(404, '井号不存在或不在数据范围内')


def item_visible(user: dict, item: dict) -> bool:
    """部室只看归口给本部室的费用项目，看不到收入。"""
    if user['role'] != 'dept':
        return True
    if item['direction'] == '收入':
        return False
    return user['dept'] in (item['owner_dept'] or '').split('/')
