"""示例数据。

成本项目树与 W001 的 2026 年 8 月金额取自《单井经营情况导出模板》示例井（见 data/cost_tree.json），
W002–W004 按系数缩放；合同、条款、单价均为示例，不代表真实结算价。
"""
import hashlib
import json
from pathlib import Path

from . import engine
from .accounts import DEMO_USERS
from .db import jdump, log, money

DATA = Path(__file__).resolve().parent / 'data' / 'cost_tree.json'

MUD = 'C2-1-02-01'
RATIO = {'C2-3-29': 0.008, 'C2-3-30': 0.02, 'C2-3-31': 0.003}
TEMPLATE_DEPTH = 4411           # 示例井 8 月进尺，用于反算泥浆服务米价与形象收入米价
MUD_M = [820, 940, 1010, 860, 781]
MUD_DAYS = ['08-06', '08-12', '08-18', '08-24', '08-30']
STD_HOURS = {'钻进': 16, '起下钻': 4, '循环': 2, '接单根': 1.5, '其他': 0.5}

USERS = DEMO_USERS   # 四个演示身份，调试账号 test 登录后可切换
TEAMS = [
    ('t1', '70101', '70101 钻井队', 'ZJ50D'), ('t2', '70102', '70102 钻井队', 'ZJ50D'),
    ('t3', '70103', '70103 钻井队', 'ZJ70D'), ('t4', '70104', '70104 钻井队', 'ZJ40'),
]
# 井号, 区块, 井型, 井别, 设计井深, 系数k, 开次段 [(开次, 队伍, 起, 止)]
WELLS = [
    ('W001', 'A区块', '直井', '开发井', 5200, 1.0, [('一开', 't1', '2026-07-20', '2026-07-26'), ('二开', 't1', '2026-07-27', None)]),
    ('W002', 'A区块', '定向井', '开发井', 4300, 0.82, [('一开', 't2', '2026-07-10', '2026-07-14'), ('二开', 't2', '2026-07-15', None)]),
    ('W003', 'B区块', '直井', '评价井', 3200, 0.64, [('一开', 't4', '2026-07-20', '2026-07-23'), ('二开', 't4', '2026-07-24', None)]),
    ('W004', 'B区块', '水平井', '开发井', 5900, 1.18, [('一开', 't3', '2026-07-01', '2026-07-06'), ('二开', 't3', '2026-07-07', '2026-08-15'), ('三开', 't3', '2026-08-16', None)]),
]
DEPT_FULL = {'市场': '市场部', '技术': '工程技术部', '物资': '物资装备部', '装备': '物资装备部', '安全': '安全环保部',
             '人事': '人力资源部', '财务': '企划财务部'}


def run(conn) -> None:
    td = json.loads(DATA.read_text(encoding='utf-8'))
    tree, rev = td['tree'], td['rev']

    conn.executemany('INSERT INTO app_user (user_id, name, role, who, team_id, dept, scope_label) VALUES (?,?,?,?,?,?,?)', USERS)
    conn.executemany("INSERT INTO team VALUES (?,?,?,?, '[二级单位]', '[队长]')", TEAMS)
    for wid, block, wtype, cat, design, k, segs in WELLS:
        conn.execute(
            "INSERT INTO well VALUES (?, '西北', ?, ?, ?, ?, ?, 0, '[甲方单位]', ?, '正钻')",
            (wid, block, cat, wtype, len(segs), design, money(rev['contract'] * k)))
    seed_well_info(conn)

    _cost_items(conn, tree)
    conn.executemany('INSERT INTO material VALUES (?,?,?,?)', [
        ('[物资编码]-01', '柴油', '吨', 'C2-1-04'), ('[物资编码]-02', '网电', '千瓦时', 'C2-1-05-01'),
        ('[物资编码]-03', '泥浆材料', '吨', 'C2-1-02-01'), ('[物资编码]-04', '钻头', '只', 'C2-1-01-01')])
    conn.executemany('INSERT INTO unit_of_measure VALUES (?,?)', [
        ('米', '进尺'), ('天', '日费、服务日'), ('队天', '定额日费、分摊基数'), ('工日', '人员出勤'),
        ('吨', '柴油、泥浆材料'), ('千瓦时', '网电'), ('只', '钻头'), ('元', '比例计提基数')])

    _rules(conn)
    _history(conn, tree, rev)
    _reports(conn)

    wells = [w[0] for w in WELLS]
    engine.recalc(conn, wells, ['2026-08', '2026-10'], '初始化', '系统')


# 基层队登记的单井基础信息：节点时间与上面的开次时段一致，提交后由系统推导施工时段
INFO = {
    # 井号: (搬安, [(开钻, 完钻)...], 人员 合同化/市场化/第三方)
    'W001': ('2026-07-17 08:00', [('2026-07-20 08:00', '2026-07-26 18:00'), ('2026-07-27 06:00', None)], (18, 12, 16)),
    'W002': ('2026-07-07 09:00', [('2026-07-10 10:00', '2026-07-14 20:00'), ('2026-07-15 08:00', None)], (16, 10, 18)),
    'W003': ('2026-07-17 07:30', [('2026-07-20 12:00', '2026-07-23 22:00'), ('2026-07-24 09:00', None)], (15, 9, 14)),
    'W004': ('2026-06-27 08:00', [('2026-07-01 06:00', '2026-07-06 16:00'), ('2026-07-07 03:00', '2026-08-15 21:00'),
                                  ('2026-08-16 10:00', None)], (20, 12, 20)),
}


def seed_well_info(conn) -> None:
    """以各队统计员身份登记并提交示例井的基础信息（不触发重算：推导出的时段与示例时段相同）。"""
    from datetime import datetime, timedelta

    from . import wellinfo
    for wid, (move, secs, staff) in INFO.items():
        w = conn.execute('SELECT * FROM well WHERE well_id=?', (wid,)).fetchone()
        if not w:
            continue
        tid = next(x[6][-1][1] for x in WELLS if x[0] == wid)
        team = conn.execute('SELECT * FROM team WHERE team_id=?', (tid,)).fetchone()
        user = {'team_id': tid, 'name': '统计员', 'who': f"{team['name']} · 统计员"}
        ms = {'搬安': move}
        for i, (a, b) in enumerate(secs, start=1):
            ms[f'开钻{i}'], ms[f'完钻{i}'] = a, b
        body = {'well_id': wid, 'period': '2026-08', 'market': w['market'], 'captain': team['captain'], 'rig_type': team['rig_type'],
                'client': w['client'], 'block': w['block'], 'well_category': w['well_category'], 'well_type': w['well_type'],
                'spud_sections': len(secs), 'design_depth': w['design_depth'], 'milestones': ms,
                'staff': dict(zip(['合同化', '市场化', '第三方'], staff))}
        rid = wellinfo.save_draft(conn, body, user)
        at = datetime.strptime(move, '%Y-%m-%d %H:%M') - timedelta(days=1)
        wellinfo.submit(conn, rid, user, at=at, recalc=False)
        conn.execute('UPDATE well_info_report SET created_at=?, updated_at=? WHERE report_id=?', (at.strftime('%Y-%m-%d %H:%M'),) * 2 + (rid,))


def _cost_items(conn, tree) -> None:
    conn.execute("INSERT INTO cost_item VALUES ('R', '收入', NULL, 1, 0, '收入', NULL, NULL, NULL, NULL, 0, NULL)")
    conn.execute("INSERT INTO cost_item VALUES ('R01', '合同收入', 'R', 2, 1, '收入', NULL, 'D 合同', '市场', 'O', 1, '[会计科目]')")
    conn.execute("INSERT INTO cost_item VALUES ('R02', '形象收入', 'R', 2, 1, '收入', NULL, 'D 按进度', '市场', 'P', 2, '[会计科目]')")
    for i, n in enumerate(tree):
        conn.execute(
            'INSERT INTO cost_item VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (n['c'], n['n'], n.get('p'), n['lv'], 1 if n['leaf'] else 0, '成本', '固定' if n['b'] == 'F' else '变动',
             n.get('t'), n.get('d'), n.get('col'), 10 + i, '[会计科目]' if n['leaf'] else None))


def _doc(conn, doc_type, name, no, counterparty, cp_type, clause_no, page, pre, hit, post):
    doc_id = conn.execute(
        "INSERT INTO source_doc (doc_type, doc_no, doc_name, counterparty, counterparty_type, file_hash, parse_status) "
        "VALUES (?,?,?,?,?,?, '已解析')", (doc_type, no, name, counterparty, cp_type, 'sha256:' + hashlib.sha256(name.encode()).hexdigest()[:16])).lastrowid
    return conn.execute('INSERT INTO clause (doc_id, clause_no, page_no, text_pre, text_hit, text_post) VALUES (?,?,?,?,?,?)',
                        (doc_id, clause_no, page, pre, hit, post)).lastrowid


def _rule(conn, code, name, item, clause_id, fact_type, method, price, unit, order, valid_from, status, origin,
          conds=(), params=None, base=None, combine='互斥', gran='逐日', priority=0, conf=None, levels=None):
    rid = conn.execute(
        'INSERT INTO economic_rule (rule_code, version, name, item_code, clause_id, fact_type, pricing_method, base_item_code, '
        'calc_order, unit_price, unit, calc_params, combine_mode, granularity, priority, valid_from, status, origin, '
        'ai_confidence, field_levels, confirmed_by, confirmed_at, published_at) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (code, name, item, clause_id, fact_type, method, base, order, price, unit, jdump(params or {}), combine, gran,
         priority, valid_from, status, origin, conf, jdump(levels or {}),
         '工程技术部 · [确认人]' if status == '已发布' else None,
         valid_from + ' 09:00:00' if status == '已发布' else None,
         valid_from + ' 10:00:00' if status == '已发布' else None)).lastrowid
    for d, op, v in conds:
        conn.execute('INSERT INTO rule_condition (rule_id, dimension, operator, value) VALUES (?,?,?,?)', (rid, d, op, v))
    log(conn, '规则', rid, '发布' if status == '已发布' else '提交', '系统' if origin != 'AI提取' else 'AI 解析')
    return rid


def _rules(conn) -> None:
    mud_price = round(1832104.12 / TEMPLATE_DEPTH, 4)
    image_price = round(7147828.84 / TEMPLATE_DEPTH, 4)

    c = _doc(conn, '内部服务合同', '《[泥浆技术服务合同]》', 'NJ-2026-018', '渤钻泥浆公司', '内部', '第4条', 'p.6',
             '', f'钻井液服务按进尺 {mud_price:,.2f} 元/米结算', '，以日报进尺为准。')
    _rule(conn, 'RL-' + MUD, '渤钻泥浆服务（按进尺）', MUD, c, '进尺', '单价', mud_price, '米', 2, '2026-08-01', '已发布', '历史反算',
          conds=[('市场', '等于', '西北'), ('开次', '属于', '二开,三开')])

    c = _doc(conn, '甲方合同', '《[甲方钻井主合同]》', 'JF-2026-003', '[甲方单位]', '外部', '第3.1条', 'p.5',
             '工程进度款按', f'当月进尺 {image_price:,.2f} 元/米确认形象收入', '，按月结算。')
    _rule(conn, 'RL-R02', '形象收入（按进尺）', 'R02', c, '进尺', '单价', image_price, '米', 1, '2026-10-01', '已发布', '历史反算',
          conds=[('市场', '等于', '西北')])

    for code, ratio, doc, no, txt in [
        ('C2-3-29', 0.008, '《[安全生产费用提取办法]》', '第3条', '按形象收入的 0.8% 提取安全生产费用'),
        ('C2-3-30', 0.02, '《[科研经费提取办法]》', '第2条', '按形象收入的 2% 提取科研经费'),
        ('C2-3-31', 0.003, '《[税金核算口径]》', '第1条', '按形象收入的 0.3% 计提税金')]:
        c = _doc(conn, '公司制度', doc, None, None, None, no, None, '', txt, '。')
        name = {'C2-3-29': '安全计提', 'C2-3-30': '科研计提', 'C2-3-31': '税金'}[code]
        _rule(conn, 'RL-' + code, name, code, c, None, '比例', ratio, '元', 3, '2026-08-01', '已发布', '历史反算', base='R02',
              gran='按期间')

    c = _doc(conn, '内部结算价', '《[公司内部结算价目表]》', 'NB-2026', None, None, '第2.1条', 'p.3',
             '', '钻井队制造费按 410,000 元/月、折合 13,666.67 元/队天分摊到井', '。')
    _rule(conn, 'RL-C1-05', '制造费（定额日费）', 'C1-05', c, '日历天', '定额日费', 13666.6667, '队天', 2, '2026-10-01', '已发布', '历史反算',
          params={'note': '41 万元/月 ÷ 30'})
    c = _doc(conn, '公司制度', '《[用工成本核算办法]》', None, None, None, '第5条', 'p.4', '合同化、市场化职工', '按出勤工日 400 元/工日计入单井人工成本', '。')
    _rule(conn, 'RL-C1-01-01', '合同化·市场化职工（按工日）', 'C1-01-01', c, '人员出勤', '单价', 400, '工日', 2, '2026-10-01', '已发布', '历史反算',
          params={'line': 'C1-01-01'})
    c = _doc(conn, '采购合同', '《[柴油年度采购合同]》', 'CG-2026-112', '[供油单位]', '外部', '第6条', 'p.3', '', '柴油结算价 7,650 元/吨', '，按出库计量。')
    _rule(conn, 'RL-C2-1-04', '柴油（量×价）', 'C2-1-04', c, '材料消耗', '单价', 7650, '吨', 2, '2026-10-01', '已发布', '人工录入',
          params={'line': 'C2-1-04'})
    c = _doc(conn, '采购合同', '《[网电供电协议]》', 'GD-2026-07', '[供电单位]', '外部', '第3条', 'p.2', '', '网电电价 0.68 元/千瓦时', '。')
    _rule(conn, 'RL-C2-1-05-01', '电费（量×价）', 'C2-1-05-01', c, '材料消耗', '单价', 0.68, '千瓦时', 2, '2026-10-01', '已发布', '人工录入',
          params={'line': 'C2-1-05-01'})
    c = _doc(conn, '公司制度', '《[用工成本核算办法]》', None, None, None, '第6条', 'p.4', '第三方用工', '按出勤工日 320 元/工日计入单井人工成本', '。')
    _rule(conn, 'RL-C1-01-02', '第三方用工（按工日）', 'C1-01-02', c, '人员出勤', '单价', 320, '工日', 2, '2026-10-01', '已发布', '历史反算',
          params={'line': 'C1-01-02'})
    c = _doc(conn, '公司制度', '《[井队伙食补助标准]》', None, None, None, '第2条', 'p.1', '井队在岗人员', '伙食费按出勤 55 元/人·天', '，不分用工类型。')
    _rule(conn, 'RL-C2-3-18', '伙食费（按全部出勤）', 'C2-3-18', c, '人员出勤', '单价', 55, '人·天', 2, '2026-10-01', '已发布', '历史反算')
    c = _doc(conn, '采购合同', '《[生产用水供应协议]》', 'GS-2026-04', '[供水单位]', '外部', '第4条', 'p.2', '', '生产用水 12 元/方', '，按拉运计量。')
    _rule(conn, 'RL-C2-3-17-01', '用水（量×价）', 'C2-3-17-01', c, '材料消耗', '单价', 12, '方', 2, '2026-10-01', '已发布', '人工录入',
          params={'line': 'C2-3-17-01'})
    c = _doc(conn, '租赁合同', '《[顶驱租赁合同]》', 'ZL-2026-09', '[顶驱服务商]', '外部', '第5条', 'p.3', '', '顶驱日租金 6,800 元/天', '，按在用天数结算。')
    _rule(conn, 'RL-C2-2-02-01', '顶驱租赁（按在用天）', 'C2-2-02-01', c, '设备台时', '单价', 6800, '天', 2, '2026-10-01', '已发布', '人工录入',
          params={'line': 'C2-2-02-01'})

    # AI 提取、待部室确认的三条草稿（原型中的三条）
    c = _doc(conn, '外包合同', '《定向井技术服务合同》', 'DX-2026-021', '[定向服务商]', '外部', '第5.3条', 'p.12',
             '定向井技术服务按日计费，', '日费 18,500 元/天，井深超过 5000 米部分上浮 20%', '。')
    _rule(conn, 'RL-C2-3-04', '定向服务日费', 'C2-3-04', c, '外包服务', '单价', 18500, '天', 2, '2026-10-01', '待确认', 'AI提取',
          params={'factor_rules': [{'dimension': '井深', 'operator': '大于', 'value': 5000, 'factor': 1.2}], 'line': 'C2-3-04'},
          combine='叠加', conf=0.93,
          levels={'科目': '高', '触发事实': '高', '计价方式': '高', '单价': '高', '适用条件': '中', '组合方式': '中', '计算粒度': '高'})
    c = _doc(conn, '甲方合同', '《[甲方钻井主合同]》补充条款', 'JF-2026-003-B1', '[甲方单位]', '外部', '第4.2条', 'p.8',
             '', '三开钻井日费按 26,000 元/天结算', '，不足一天按一天计。')
    _rule(conn, 'RL-R02-D', '钻井日费', 'R02', c, '日历天', '单价', 26000, '天', 1, '2026-10-01', '待确认', 'AI提取',
          conds=[('开次', '等于', '三开')], conf=0.88,
          levels={'科目': '高', '触发事实': '高', '计价方式': '高', '单价': '高', '适用条件': '中', '组合方式': '高', '计算粒度': '高'})
    c = _doc(conn, '采购合同', '《钻头采购框架协议》', 'ZT-2026-005', '[钻头供应商]', '外部', '第3.4条', 'p.6',
             '', '水平井钻头费用按单井包干，每口井 160,000 元', '，超出部分由供方承担。')
    _rule(conn, 'RL-C2-1-01-01', '钻头单井包干', 'C2-1-01-01', c, '单井完钻', '包干', 160000, '井', 2, '2026-10-01', '待确认', 'AI提取',
          conds=[('井型', '等于', '水平井')], priority=10, gran='按井', conf=0.62,
          levels={'科目': '高', '触发事实': '低', '计价方式': '中', '单价': '高', '适用条件': '高', '组合方式': '低', '计算粒度': '高'})


def _history(conn, tree, rev) -> None:
    """8 月为期初历史数据：按总表列转行，以“历史导入”写入经济事项。泥浆服务与比例计提由规则计算。"""
    batch = conn.execute(
        "INSERT INTO calc_batch (trigger, scope, engine_version, started_at, finished_at, operator) "
        "VALUES ('历史导入', '{\"period\": \"2026-08\"}', 'import-1.0', '2026-09-05 09:00:00', '2026-09-05 09:01:00', '企划财务部')").lastrowid
    for wid, *_rest in WELLS:
        k = _rest[4]
        rows = [('R02', rev['image'] * k, '收入', None, '市场', 'P')]
        for n in tree:
            if n['leaf'] and n['c'] != MUD and n['c'] not in RATIO and n.get('a'):
                rows.append((n['c'], n['a'] * k, '成本', '固定' if n['b'] == 'F' else '变动', n.get('d'), n.get('col')))
        for code, amt, direction, beh, dept, col in rows:
            conn.execute(
                "INSERT INTO economic_event (event_date, period, well_id, item_code, direction, cost_behavior, owner_dept, "
                "origin_type, amount, batch_id, status, source_note) VALUES ('2026-08-31', '2026-08', ?,?,?,?,?, '历史导入', ?,?, '有效', ?)",
                (wid, code, direction, beh, dept, money(amt), batch, f'全要素经营总表 · 2026-08 · 总表 {col} 列'))


def _report(conn, wid, tid, date, phase, d0, d1, status, lines=None, hours=None, reporter='[填报人]', notes=None):
    rid = conn.execute(
        'INSERT INTO daily_report (well_id, team_id, report_date, status, phase, depth_start, depth_end, hours, lines, line_notes, reporter, '
        'submitted_at, reviewer, reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (wid, tid, date, status, phase, d0, d1, jdump(hours or STD_HOURS), jdump(lines or {}), jdump(notes or {}), reporter,
         date + ' 20:00:00' if status != '草稿' else None,
         '工程技术部 · [审核人]' if status == '已审核' else None,
         date + ' 22:00:00' if status == '已审核' else None)).lastrowid
    if status in ('已提交', '已审核'):
        from .reports import facts_from_report
        facts_from_report(conn, rid, '已审核' if status == '已审核' else '已提交')
    return rid


CREW_W001 = {'C1-01-01': 30, 'C1-01-02': 16, 'C2-2-02-01': 1}   # 合同化·市场化 30 人、第三方 16 人、顶驱在用


def _reports(conn) -> None:
    team_of = {w[0]: w[6][-1][1] for w in WELLS}
    for wid, _b, _t, _c, _d, k, segs in WELLS:
        depth = 0.0
        for m, day in zip(MUD_M, MUD_DAYS):
            q = round(m * k)
            date = '2026-' + day
            phase = [s[0] for s in segs if s[2] <= date and (s[3] is None or s[3] >= date)][-1]
            _report(conn, wid, team_of[wid], date, phase, depth, depth + q, '已审核')
            depth += q
        conn.execute('UPDATE well SET actual_depth=? WHERE well_id=?', (depth, wid))

    # W001 十月：前 8 天已审核，10-09 草稿（原型中的日报示例）
    depth = TEMPLATE_DEPTH
    for i, step in enumerate([52, 48, 61, 0, 44, 57, 50, 53]):
        date = f'2026-10-{i + 1:02d}'
        hours = STD_HOURS if step else {'钻进': 0, '起下钻': 10, '循环': 6, '接单根': 0, '其他': 8}
        _report(conn, 'W001', 't1', date, '二开', depth, depth + step, '已审核', hours=hours,
                lines={**CREW_W001, 'C2-1-04': 4.2 if step else 2.6, 'C2-1-05-01': 21000 if step else 9000, 'C2-3-17-01': 16},
                notes={'C2-2-02-01': 'DQ-031'})
        depth += step
    conn.execute("UPDATE well SET actual_depth=? WHERE well_id='W001'", (depth,))
    _report(conn, 'W001', 't1', '2026-10-09', '二开', depth, depth + 53, '草稿',
            hours={'钻进': 16, '起下钻': 4, '循环': 2, '接单根': 1.5, '其他': 0.5},
            lines={**CREW_W001, 'C2-1-04': 4.6, 'C2-1-05-01': 22000, 'C2-3-17-01': 18}, notes={'C2-2-02-01': 'DQ-031'})
    # W004 十月 1 天已提交、待部室审核
    _report(conn, 'W004', 't3', '2026-10-08', '三开', 5210, 5262, '已提交', lines={'C1-01-01': 32, 'C1-01-02': 20, 'C2-1-04': 5.1, 'C2-3-17-01': 20})
