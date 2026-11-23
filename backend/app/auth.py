"""角色、菜单与功能权限。

功能权限：能做什么（编辑 / 审核 / 查看），决定菜单和按钮是否出现。
数据权限：能看哪些行。基层按队伍过滤，下钻每一层都生效；管理端看全部。

费用规则入库分三级，后端按角色拒绝越权操作：
  管理端（普通账户）提交 → 费用管理部室确认或退回 → 系统管理员发布。

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
        {'label': '经营', 'items': [{'id': 'overview', 'label': '经营总览'},
                                  {'id': 'quadmap', 'label': '四维经营地图'},
                                  {'id': 'benchmark', 'label': '对标分析'}, {'id': 'costdetail', 'label': '费用导出'}]},
        {'label': '规则', 'items': [{'id': 'masterdata', 'label': '主数据维护'}, {'id': 'rules', 'label': '规则库'},
                                  {'id': 'allocation', 'label': '费用分摊'}, {'id': 'review', 'label': '日报监控'}]},
        {'label': '系统', 'items': [{'id': 'perm', 'label': '角色与权限'}, {'id': 'batches', 'label': '历史记录'}]},
    ],
    'team': [
        {'label': '填报', 'items': [{'id': 'wellreg', 'label': '新开井登记'}, {'id': 'report', 'label': '成本日报'}]},
        {'label': '经营', 'items': [{'id': 'overview', 'label': '本队单井收支'},
                                  {'id': 'quadmap', 'label': '四维经营地图'}]},
    ],
    'cost': [
        {'label': '规则', 'items': [{'id': 'rules', 'label': '规则库'}]},
    ],
    'admin': [
        {'label': '规则', 'items': [{'id': 'rules', 'label': '规则库'}]},
        {'label': '经营', 'items': [{'id': 'quadmap', 'label': '四维经营地图'}]},
        {'label': '系统', 'items': [{'id': 'perm', 'label': '角色与权限'}]},
    ],
    'dept': [
        {'label': '归口', 'items': [{'id': 'masterdata', 'label': '归口科目'}, {'id': 'rules', 'label': '归口规则'},
                                  {'id': 'basis', 'label': '变化依据'}]},
    ],
}

ROLE_NAMES = {'finance': '管理端', 'team': '填报端', 'cost': '费用管理部室', 'admin': '系统管理员', 'dept': '归口部室'}
# 权限矩阵列顺序，与 PERMS 每个功能的权限列表一一对应
ROLE_ORDER = ('team', 'finance', 'cost', 'admin', 'dept')
# 能打开规则库的角色。提交、确认、发布各自再校验，不能互相代替。
# 确认只在归口部室接口上放行，并且再按本部室科目核对。
RULE_ACTORS = ('finance', 'cost', 'admin')
RULE_READERS = ('finance', 'cost', 'admin', 'dept')

# 功能 → 各角色权限（team, finance, cost, admin, dept）。
# 规则入库：管理端以普通账户提交，归口部室确认或退回本部室规则，系统管理员发布。
# 归口部室不审核日报、不发布、不重算。没有单独的费用管理部室登录账号。
PERMS = [
    ('成本日报填报（系统校验通过即生效）', ['编辑', '查看', '', '', '']),
    ('日报监控：抽查确认、退回更正、冲销退回', ['', '编辑', '', '', '']),
    ('费用规则提交（普通账户起草、修改草稿并提交）', ['', '编辑', '', '', '']),
    ('费用规则确认与退回（归口部室，仅本部室）', ['', '', '', '', '审核']),
    ('费用规则发布：影响预览、发布、回滚（系统管理员）', ['', '', '', '编辑', '']),
    ('新开井登记（基础信息填报，含人员数量，人工费按此核算）', ['编辑', '查看', '', '', '']),
    ('基础信息库（新开井登记页内，本队井）', ['查看', '', '', '', '']),
    ('单井基础信息汇总、导入导出、退回修改', ['', '编辑', '', '', '']),
    ('费用科目树与主数据', ['查看', '编辑', '', '', '查看']),
    ('计算批次与手工重算', ['', '编辑', '', '', '']),
    ('费用池与费用分摊（折旧、长摊、项目部运行、制造费）', ['', '编辑', '', '', '']),
    ('经营报表与下钻', ['查看', '查看', '', '', '']),
    ('区块四维经营地图（只读，按本角色可见井汇总）', ['查看', '查看', '', '查看', '']),
    ('区块四维地图：阈值与战略标记（管理端、系统管理员）', ['', '编辑', '', '编辑', '']),
    ('对标分析', ['', '查看', '', '', '']),
    ('费用导出（按时间、市场、区块、队伍）', ['', '导出', '', '', '']),
    ('单井经营结果模板导入', ['', '编辑', '', '', '']),
    ('角色与权限', ['', '编辑', '', '查看', '']),
    ('查看账号当前密码', ['', '', '', '查看', '']),
    ('归口科目、规则与已入账结果（仅本部室；规则可确认，不能发布）', ['', '', '', '', '查看']),
    ('变化依据（提交合同、通知、会议纪要；不写规则、不算金额）', ['', '', '', '', '编辑']),
]


def deny_company_money(user: dict) -> None:
    """归口部室只看本部室已入账结果，不看全公司汇总，也不触发计算。"""
    if user['role'] == 'dept':
        raise HTTPException(403, '归口部室不能查看全公司金额，也不能发起计算')


def current_user(authorization: str | None = Header(default=None), x_user_id: str | None = Header(default=None)) -> dict:
    conn = connect()
    try:
        if authorization and authorization.startswith('Bearer '):
            u = accounts.user_by_token(conn, authorization[7:].strip())
            if not u:
                raise HTTPException(401, '登录已过期，请重新登录')
            return u
        if os.environ.get('TFOS_DEMO_AUTH') == '1' and x_user_id:
            r = conn.execute('SELECT * FROM app_user WHERE user_id=?', (x_user_id,)).fetchone()
            if r:
                return accounts.without_secrets(dict(r))
        raise HTTPException(401, '请先登录')
    finally:
        conn.close()


def require(user: dict, *roles: str) -> None:
    if user['role'] not in roles:
        raise HTTPException(403, '当前角色无此功能权限')


def visible_wells(conn, user: dict) -> list[str]:
    if user['role'] == 'team':
        return [r['well_id'] for r in conn.execute(
            'SELECT DISTINCT well_id FROM team_well_period WHERE team_id=? AND well_id NOT IN (SELECT well_id FROM well_deleted) '
            'ORDER BY well_id', (user['team_id'],))]
    return [r['well_id'] for r in conn.execute(
        'SELECT well_id FROM well WHERE well_id NOT IN (SELECT well_id FROM well_deleted) ORDER BY well_id')]


def check_well(conn, user: dict, well_id: str) -> None:
    if well_id not in visible_wells(conn, user):
        raise HTTPException(404, '井号不存在或不在数据范围内')
