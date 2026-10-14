"""成本日报报量项目字典。

依据《核心数据库表设计》附录 A 的计价类型与 business_fact 事实类型，只收录“需要基层采集、参与成本计算”的量：
  人员出勤不再由基层逐日填报：按基础信息中的人员数量自动核算（见 STAFF_LINES）
  C 量×价            → 材料消耗（柴油、润滑油、电、水）
  F 租赁（按在用天）  → 设备台时（钻具、井控、设备租赁）
  D 服务计价          → 外包服务（技术服务、运输、环保），须附签证单号
进尺与工况由钻井日报预填、基层确认，单独成段。其余成本项目不由基层填报，原因见 NOT_COLLECTED。
报量行以末级费用项目编码为键，规则 calc_params.line 引用同一编码。
"""
from .db import jload

SECTIONS = [
    {'id': 'drill', 'title': '进尺与工况', 'fact': '进尺 · 工况时间', 'how': '钻井日报接口预填，基层确认',
     'drives': '形象收入、渤钻泥浆服务、定向与提速（按米计价）；工况用于校验和规则条件'},
    {'id': 'energy', 'title': '能耗与材料消耗', 'fact': '材料消耗', 'how': '按计量表读数或领用量',
     'drives': '柴油、润滑油、网电电费、水费'},
    {'id': 'equip', 'title': '设备与工具在用', 'fact': '设备台时（天）', 'how': '当天在用记 1 天，多套按套数累计；填设备编号',
     'drives': '钻具租赁、井控、设备租赁费'},
    {'id': 'service', 'title': '外包技术服务', 'fact': '外包服务', 'how': '按服务量填写，必须填签证单号',
     'drives': '外部泥浆、定向、提速、钻井工具、固井、下套管、试压、录井、测井、欠平衡、井下作业、钻头服务'},
    {'id': 'logistics', 'title': '运输与环保', 'fact': '外包服务', 'how': '按台班、趟、方量填写，必须填单据号',
     'drives': '运输费、环保费用、泥浆不落地、冬防保温、架线'},
]

# (编码, 段, 分组, 名称, 单位, 事实类型, 需单据, 默认显示)
_L = []


def _add(section, group, fact_type, unit, attach, rows, core=()):
    for code, label in rows:
        _L.append((code, section, group, label, unit, fact_type, attach, code in core))


_add('energy', '燃料与润滑', '材料消耗', '吨', 0, [('C2-1-04', '柴油')], core=('C2-1-04',))
_add('energy', '燃料与润滑', '材料消耗', '升', 0, [('C2-1-03', '润滑油')])
_add('energy', '电', '材料消耗', '千瓦时', 0, [('C2-1-05-01', '网电用电量')], core=('C2-1-05-01',))
_add('energy', '水', '材料消耗', '方', 0, [('C2-3-17-01', '用水（市场）'), ('C2-3-17-02', '用水（安全）')], core=('C2-3-17-01',))
_add('equip', '钻具', '设备台时', '天', 0, [('C2-1-08-02', '租赁钻具')])
_add('equip', '井控', '设备台时', '天', 0, [('C2-1-07-02', '井控工具'), ('C2-1-07-03', '井控装置'), ('C2-1-07-04', '租赁井控设备')])
_add('equip', '设备租赁', '设备台时', '天', 0, [('C2-2-02-01', '顶驱'), ('C2-2-02-02', '网电设备'), ('C2-2-02-03', '动力外包')])
_add('service', '钻井液', '外包服务', '天', 1, [('C2-1-02-02', '外部泥浆服务')])
_add('service', '钻头', '外包服务', '只', 1, [('C2-1-01-02', '钻头服务'), ('C2-1-01-03', '钻头修复')])
_add('service', '定向井服务', '外包服务', '天', 1, [('C2-3-04', '定向井服务')])
_add('service', '提速服务', '外包服务', '天', 1,
     [('C2-3-05-01', '扭力冲击器'), ('C2-3-05-02', '水力振荡器'), ('C2-3-05-03', '垂直钻井'), ('C2-3-05-04', '旋转导向'),
      ('C2-3-05-05', '振荡螺杆'), ('C2-3-05-06', '其他提速工具')])
_add('service', '钻井工具', '外包服务', '天', 1,
     [('C2-3-06-01', '扩眼工具'), ('C2-3-06-02', '取芯工具'), ('C2-3-06-03', '震击器'), ('C2-3-06-04', '减震器'),
      ('C2-3-06-05', '刮壁器'), ('C2-3-06-06', '分隔器'), ('C2-3-06-07', '螺杆'), ('C2-3-06-08', '其他钻井工具')])
_add('service', '固井与套管', '外包服务', '井次', 1, [('C2-3-11', '固井'), ('C2-3-08', '下套管服务'), ('C2-3-12', '试压')])
_add('service', '录井与测井', '外包服务', '天', 1, [('C2-3-13', '录井')])
_add('service', '录井与测井', '外包服务', '井次', 1, [('C2-3-14', '测井')])
_add('service', '其他技术服务', '外包服务', '天', 1, [('C2-3-10', '欠平衡 / 控压')])
_add('service', '井下作业', '外包服务', '井次', 1,
     [('C2-3-23-01', '试油作业'), ('C2-3-23-02', '试气作业'), ('C2-3-23-03', '压裂作业'), ('C2-3-23-04', '其他井下作业')])
_add('logistics', '运输', '外包服务', '次', 1, [('C2-3-01-01', '钻机搬迁')])
_add('logistics', '运输', '外包服务', '台班', 1,
     [('C2-3-01-02', '吊车'), ('C2-3-01-03', '货车'), ('C2-3-01-04', '机具'), ('C2-3-01-05', '生产保障用车')])
_add('logistics', '运输', '外包服务', '趟', 1, [('C2-3-01-06', '其他运输')])
_add('logistics', '环保', '外包服务', '方', 1, [('C2-3-02-01', '污水处理'), ('C2-3-02-03', '固废处理')])
_add('logistics', '环保', '外包服务', '车次', 1, [('C2-3-02-02', '垃圾处理')])
_add('logistics', '环保', '外包服务', '吨', 1, [('C2-3-02-04', '危废处理')])
_add('logistics', '环保', '外包服务', '次', 1, [('C2-3-02-06', '完井环保')])
_add('logistics', '泥浆不落地', '外包服务', '方', 1, [('C2-3-25-01', '泥浆不落地（市场）'), ('C2-3-25-02', '泥浆不落地（安全）')])
_add('logistics', '冬防保温', '外包服务', '天', 1, [('C2-3-24-02', '第三方保温')])
_add('logistics', '架线', '外包服务', '次', 1, [('C2-1-05-02', '网电架线')])

CATALOG = _L

# 不由基层填报的成本项目：编码前缀 → 原因
NOT_COLLECTED = [
    ('系统按施工期每天自动生成队天，不用填', ['C1-02', 'C1-03', 'C1-04', 'C1-05', 'C2-3-22']),
    ('由进尺自动计算', ['C2-1-02-01']),
    ('按基础信息中的人员数量自动核算', ['C1-01', 'C2-3-18', 'C2-3-19', 'C2-3-20']),
    ('物资系统出库单接口带入', ['C2-1-01-01', 'C2-1-06', 'C2-3-26', 'C2-3-27']),
    ('财务按期限或进尺摊销', ['C2-1-07-01', 'C2-1-08-01']),
    ('按形象收入比例计提', ['C2-3-29', 'C2-3-30', 'C2-3-31']),
    ('归口部室凭发票、签证金额据实或人工计价', ['C2-2-01', 'C2-3-03', 'C2-3-07', 'C2-3-09', 'C2-3-15', 'C2-3-16', 'C2-3-21',
                                     'C2-3-28', 'C2-1-08-03', 'C2-1-08-04', 'C2-3-02-05', 'C2-3-02-07', 'C2-3-24-01']),
]

# 人员数量取自基础信息：(报量项目编码, 名称, 基础信息中的用工类型)；每天的人员出勤事实 = 当时基础信息中的在册人数
STAFF_LINES = [('C1-01-01', '合同化·市场化职工', ('合同化', '市场化')), ('C1-01-02', '第三方用工', ('第三方',))]
STAFF_UNIT = '工日'
# 旧版日报里逐日填报的人员项目：不再收，历史日报里的数据忽略
RETIRED = ('C1-01-', 'C2-3-20', '人员出勤')


def retired(key: str | None) -> bool:
    return bool(key) and canon(key).startswith(RETIRED)


# 旧版日报的行名 → 现行报量项目编码
LEGACY = {'柴油': 'C2-1-04', '网电': 'C2-1-05-01', '定向服务': 'C2-3-04'}


def canon(key: str | None) -> str | None:
    return LEGACY.get(key, key) if key else key


def canon_map(d: dict | None) -> dict:
    out: dict = {}
    for k, v in (d or {}).items():
        k = canon(k)
        if retired(k):
            continue
        out[k] = v if k not in out else out[k]
    return out


def seed(conn) -> None:
    """建库或旧库升级时写入报量项目字典（只在表为空时写入）；旧库里已取消的人员出勤段在此清除。"""
    conn.execute("DELETE FROM report_line WHERE section='staff'")
    if conn.execute('SELECT 1 FROM report_line LIMIT 1').fetchone():
        return
    sec_order = {s['id']: i for i, s in enumerate(SECTIONS)}
    for i, (code, sec, group, label, unit, fact_type, attach, core) in enumerate(CATALOG):
        conn.execute('INSERT INTO report_line (line_key, section, section_no, group_name, label, unit, fact_type, item_code, '
                     'needs_doc, core, sort_no) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (code, sec, sec_order[sec], group, label, unit, fact_type, code, attach, 1 if core else 0, i))


def lines(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        'SELECT l.*, i.item_name, i.owner_dept, i.calc_type FROM report_line l LEFT JOIN cost_item i ON i.item_code=l.item_code '
        'WHERE l.enabled=1 ORDER BY l.section_no, l.sort_no')]


def not_collected(conn) -> list[dict]:
    collected = {r['item_code'] for r in conn.execute('SELECT item_code FROM report_line')}
    out = []
    for reason, prefixes in NOT_COLLECTED:
        items = [dict(r) for r in conn.execute(
            "SELECT item_code, item_name FROM cost_item WHERE is_leaf=1 AND direction='成本' ORDER BY sort_no")
            if any(r['item_code'] == p or r['item_code'].startswith(p + '-') for p in prefixes) and r['item_code'] not in collected]
        out.append({'reason': reason, 'items': items})
    covered = {i['item_code'] for g in out for i in g['items']} | collected
    rest = [dict(r) for r in conn.execute("SELECT item_code, item_name FROM cost_item WHERE is_leaf=1 AND direction='成本' "
                                          'ORDER BY sort_no') if r['item_code'] not in covered]
    if rest:
        out.append({'reason': '其他（归口部室确认）', 'items': rest})
    return out


def rule_line(rule: dict) -> str | None:
    return canon(jload(rule['calc_params']).get('line'))
