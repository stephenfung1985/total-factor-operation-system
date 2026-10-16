"""角色、菜单与两级权限。

功能权限：能做什么（编辑 / 审核 / 查看），决定菜单和按钮是否出现。
数据权限：能看哪些行。基层按队伍过滤，下钻每一层都生效；管理端看全部。

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
                                  {'id': 'benchmark', 'label': '对标分析'}, {'id': 'costdetail', 'label': '费用导出'},
                                  {'id': 'wellinfo', 'label': '基础信息库'}]},
        {'label': '规则', 'items': [{'id': 'masterdata', 'label': '主数据维护'}, {'id': 'rules', 'label': '规则库'},
                                  {'id': 'allocation', 'label': '费用分摊'}, {'id': 'review', 'label': '日报监控'}]},
        {'label': '系统', 'items': [{'id': 'perm', 'label': '角色与权限'}, {'id': 'batches', 'label': '历史记录'}]},
    ],
    'team': [
        {'label': '填报', 'items': [{'id': 'wellreg', 'label': '新开井登记'}, {'id': 'report', 'label': '成本日报'}]},
        {'label': '经营', 'items': [{'id': 'overview', 'label': '本队单井收支'}]},
    ],
}

ROLE_NAMES = {'finance': '管理端', 'team': '填报端'}

# 功能 → 各角色权限（team, finance）。专业部室角色已取消：部室只提供规则变化依据（文件、通知），由管理端录入维护。
PERMS = [
    ('成本日报填报（系统校验通过即生效）', ['编辑', '查看']),
    ('日报监控：抽查确认、退回更正、冲销退回', ['', '编辑']),
    ('依据文件与规则维护（按部室提供的变化依据）', ['', '编辑']),
    ('规则发布：影响预览、发布、回滚', ['', '编辑']),
    ('新开井登记（基础信息填报，含人员数量，人工费按此核算）', ['编辑', '查看']),
    ('基础信息库（本队 / 全部井）', ['查看', '查看']),
    ('单井基础信息汇总、导入导出、退回修改', ['', '编辑']),
    ('费用科目树与主数据', ['查看', '编辑']),
    ('计算批次与手工重算', ['', '编辑']),
    ('费用池与费用分摊（折旧、长摊、项目部运行、制造费）', ['', '编辑']),
    ('经营报表与下钻', ['查看', '查看']),
    ('对标分析', ['', '查看']),
    ('费用导出（按时间、市场、区块、队伍）', ['', '导出']),
    ('单井经营结果模板导入', ['', '编辑']),
    ('角色与权限', ['', '编辑']),
]


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
                return dict(r)
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
