"""四维地图。

金额只从经济事项汇总，口径与经营总览、对标分析相同，不另存一套收入成本：
  形象收入 = 科目 R02
  固定成本 = 科目 C1（固定成本树）
  变动成本 = 科目 C2（变动成本树，含安全计提、科研计提、税金）
  毛益额 = 形象收入 − 变动成本
  净利润 = 形象收入 − 总成本；总成本 = 固定成本 + 变动成本
  毛益率 = 区块毛益额合计 ÷ 区块形象收入合计
  利润率 = 区块净利润合计 ÷ 区块形象收入合计

比率按汇总金额计算，不是各井比率的平均。形象收入 ≤ 0，或没有可汇总的收入/成本事项时，
比率记为空，不写成 0%，也不判成盈利。

盈利、微利、亏损同时看利润率和毛益率，规则由阈值决定。阈值的初始值只是会计零线参考，
confirmed=0 表示尚未经经营管理确认。战略必须由管理端或系统管理员按区块显式标记，
不能从利润率自动推导，也不能盖住基础财务分类。
"""
import math
import re
from datetime import date, datetime

from fastapi import HTTPException

from . import analysis, auth
from .db import log

READ_ROLES = ('finance', 'admin', 'team')
MAINTAIN_ROLES = ('finance', 'admin')

# 初始参考：只用“是否覆盖变动成本 / 是否覆盖总成本”两条会计零线。
# 0 不是已经确认的目标利润率。迁移只在表为空时写入，不覆盖已有设置。
REFERENCE = {
    'profit_net_min': 0.0,
    'profit_gm_min': 0.0,
    'thin_net_min': None,
    'thin_gm_min': 0.0,
}
REFERENCE_NOTE = (
    '初始参考只用现有会计口径里的两条零线，不是已经批准的考核标准，也不能把 0% 理解成公司确认的目标利润率。'
    '盈利：利润率 ≥ 0 且 毛益率 ≥ 0，表示形象收入同时覆盖变动成本和总成本。'
    '微利：毛益率 ≥ 0，但利润率没有达到盈利线；初始参考下盈利线也是 0，所以微利就是利润率 < 0 且毛益率 ≥ 0，'
    '表示覆盖了变动成本、没有覆盖全部固定成本。'
    '亏损：毛益率低于微利线；初始参考下就是毛益率 < 0。利润率很高但毛益率为负时仍判亏损。'
    '利润率和毛益率都参与判断。经营管理确认前，页面上的分类只是参考。'
)
RATE_MIN, RATE_MAX = -5.0, 5.0
UNLIMITED = ('无限', '不限', '上不封顶', '不设上限', '无限制', '随便投', '敞开投')
DISCLAIMER = '这是管理决策参考，不自动下达调度，不修改钻机配置，不自动分配资金。'
RIG_COUNT_NOTE = '主数据没有独立钻机台账，只有队伍上的钻机型号。不能把队伍数当成钻机台数，钻机台数为未知。'
BUDGET_NOTE = '系统没有区块预算或资源投入金额，不显示投入金额。'
SCOPE_NOTE = {
    'team': '只汇总本队可见井。同一区块若还有其他队伍的井，这里的数字不是全区块合计，也不显示其他队伍的金额。',
    'finance': '数据范围为全部可见井，与经营总览相同。',
    'admin': '数据范围为全部可见井。系统管理员可维护阈值和战略标记，不能据此直接改钻机或分钱。',
}

FORMULA = {
    'revenue': '形象收入 = 所选期间内、本数据范围各井经济事项中科目 R02（形象收入）的金额合计。冲销已经抵消，与经营总览相同。',
    'fixed': '固定成本 = 科目 C1（一、固定成本）及其下级的金额合计。',
    'variable': '变动成本 = 科目 C2（二、变动成本）及其下级的金额合计，含安全计提、科研计提、税金。',
    'total_cost': '总成本 = 固定成本 + 变动成本。',
    'gross_margin': '毛益额 = 形象收入 − 变动成本。分子是区块毛益额合计，不是各井毛益额的平均。',
    'net_profit': '净利润 = 形象收入 − 总成本。',
    'gm_rate': '毛益率 = 区块毛益额合计 ÷ 区块形象收入合计。先把金额加总再除，不平均各井毛益率。形象收入 ≤ 0 时不计算，不显示 0%。',
    'net_rate': '利润率 = 区块净利润合计 ÷ 区块形象收入合计。同样先加总再除。形象收入 ≤ 0 时不计算，不显示 0%。',
    'source': '金额只来自经济事项，不另存收入成本事实。',
}


def ensure_defaults(conn) -> None:
    """表由 schema.sql 创建。这里只补第一行初始参考，已有记录一律不动，重复启动不会改回去。"""
    if conn.execute('SELECT 1 FROM block_quad_threshold WHERE id=1').fetchone():
        return
    conn.execute(
        'INSERT INTO block_quad_threshold (id, profit_net_min, profit_gm_min, thin_net_min, thin_gm_min, confirmed) '
        'VALUES (1, ?, ?, NULL, ?, 0)',
        (REFERENCE['profit_net_min'], REFERENCE['profit_gm_min'], REFERENCE['thin_gm_min']))


def require_read(user: dict) -> None:
    auth.require(user, *READ_ROLES)


def require_maintain(user: dict) -> None:
    auth.require(user, *MAINTAIN_ROLES)


def _near(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < 1e-9


def _ge(a: float, b: float) -> bool:
    return a > b or _near(a, b)


def _num(value, label: str, required: bool) -> float | None:
    if value is None or value == '':
        if required:
            raise HTTPException(422, f'{label}不能为空')
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f'{label}必须是数字')
    if not math.isfinite(number) or number < RATE_MIN or number > RATE_MAX:
        raise HTTPException(422, f'{label}须在 {RATE_MIN:g} 到 {RATE_MAX:g} 之间（小数，0.05 表示 5%）')
    return number


def validate_thresholds(raw: dict) -> dict:
    """两条盈利线、毛益率微利线必填；利润率微利线可空，表示不设下限。微利区间必须存在。"""
    out = {
        'profit_net_min': _num(raw.get('profit_net_min'), '盈利利润率下限', True),
        'profit_gm_min': _num(raw.get('profit_gm_min'), '盈利毛益率下限', True),
        'thin_net_min': _num(raw.get('thin_net_min'), '微利利润率下限', False),
        'thin_gm_min': _num(raw.get('thin_gm_min'), '微利毛益率下限', True),
    }
    if out['thin_net_min'] is not None and not _ge(out['profit_net_min'], out['thin_net_min']):
        raise HTTPException(422, '盈利利润率下限不能低于微利利润率下限')
    if not _ge(out['profit_gm_min'], out['thin_gm_min']):
        raise HTTPException(422, '盈利毛益率下限不能低于微利毛益率下限')
    same_net = out['thin_net_min'] is not None and _near(out['profit_net_min'], out['thin_net_min'])
    same_gm = _near(out['profit_gm_min'], out['thin_gm_min'])
    if same_net and same_gm:
        raise HTTPException(422, '盈利线和微利线完全重合，微利区间不存在。请拉高至少一条盈利线，或放低一条微利线')
    return out


def is_reference(row: dict) -> bool:
    return all(_near(row.get(k), REFERENCE[k]) for k in REFERENCE)


def threshold_state(row: dict) -> str:
    ready = row.get('profit_net_min') is not None and row.get('profit_gm_min') is not None and row.get('thin_gm_min') is not None
    if not ready:
        return 'unset'
    if row.get('confirmed'):
        return 'confirmed'
    return 'reference' if is_reference(row) else 'draft'


def load_thresholds(conn) -> dict:
    ensure_defaults(conn)
    row = dict(conn.execute('SELECT * FROM block_quad_threshold WHERE id=1').fetchone())
    state = threshold_state(row)
    labels = {
        'unset': '设置未完成',
        'reference': '初始参考，待经营管理确认',
        'draft': '已修改，待经营管理确认',
        'confirmed': '已由经营管理确认',
    }
    row.update(confirmed=bool(row['confirmed']), state=state, state_label=labels[state],
               official=state == 'confirmed', reference=state == 'reference',
               note=REFERENCE_NOTE if state == 'reference' else '')
    return row


def save_thresholds(conn, user: dict, raw: dict, confirm: bool) -> dict:
    require_maintain(user)
    values = validate_thresholds(raw)
    current = load_thresholds(conn)
    now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    confirmed = 1 if confirm else 0
    conn.execute(
        'UPDATE block_quad_threshold SET profit_net_min=?, profit_gm_min=?, thin_net_min=?, thin_gm_min=?, '
        'confirmed=?, updated_by=?, updated_at=?, confirmed_by=?, confirmed_at=? WHERE id=1',
        (values['profit_net_min'], values['profit_gm_min'], values['thin_net_min'], values['thin_gm_min'],
         confirmed, user['who'], now_text,
         user['who'] if confirm else None, now_text if confirm else None))
    action = '确认' if confirm else '保存'
    log(conn, '四维地图阈值', '1', action, user['who'],
        f"盈利线 利润率 {values['profit_net_min']}、毛益率 {values['profit_gm_min']}；"
        f"微利线 利润率 {values['thin_net_min']}、毛益率 {values['thin_gm_min']}；"
        f"原状态 {current['state_label']}")
    return load_thresholds(conn)


def withdraw_thresholds(conn, user: dict) -> dict:
    require_maintain(user)
    current = load_thresholds(conn)
    if current['confirmed']:
        conn.execute('UPDATE block_quad_threshold SET confirmed=0, confirmed_by=NULL, confirmed_at=NULL WHERE id=1')
        log(conn, '四维地图阈值', '1', '撤回确认', user['who'], '阈值改回待确认，数值未改')
    return load_thresholds(conn)


def classify(net_rate, gm_rate, quality: str, thresholds: dict) -> str:
    """盈利 / 微利 / 亏损同时用两项比率。过不了质量检查，或阈值未设完，一律不可判定。

    边界取“大于等于”。先看是否同时达到两条盈利线，再看是否同时达到两条微利线，剩下的是亏损。
    利润率微利线为空时，这一条视为不设下限，毛益率仍然要过微利线。
    """
    if quality != 'ok' or threshold_state(thresholds) == 'unset':
        return '不可判定'
    if net_rate is None or gm_rate is None:
        return '不可判定'
    if _ge(net_rate, thresholds['profit_net_min']) and _ge(gm_rate, thresholds['profit_gm_min']):
        return '盈利'
    net_ok = True if thresholds.get('thin_net_min') is None else _ge(net_rate, thresholds['thin_net_min'])
    if net_ok and _ge(gm_rate, thresholds['thin_gm_min']):
        return '微利'
    return '亏损'


def quality_of(revenue: float, has_events: bool, has_cost: bool) -> str:
    if not has_events:
        return 'no_data'
    if revenue is None or revenue <= 0:
        return 'revenue_non_positive'
    if not has_cost:
        return 'cost_unrecorded'
    return 'ok'


QUALITY_LABEL = {
    'ok': '可以计算',
    'no_data': '数据不足：所选期间没有经济事项',
    'revenue_non_positive': '不可判定：形象收入为零或为负，不能计算比率',
    'cost_unrecorded': '不可判定：有形象收入，但没有成本类经济事项，不能当成正常盈利',
}


def rates_from_sums(revenue: float, gross_margin: float, net_profit: float, quality: str):
    """只有质量为 ok 才返回比率。零收入、负收入、缺数据都不返回 0。"""
    if quality != 'ok' or revenue is None or revenue <= 0:
        return None, None
    return gross_margin / revenue, net_profit / revenue


def weighted_rates(parts: list[tuple[float, float, float]]) -> dict:
    """parts 为 (形象收入, 毛益额, 净利润)。比率用合计，并给出各段比率的简单平均，便于测试对照。"""
    revenue = sum(p[0] for p in parts)
    gross = sum(p[1] for p in parts)
    net = sum(p[2] for p in parts)
    usable = [p for p in parts if p[0] > 0]
    avg_gm = sum(p[1] / p[0] for p in usable) / len(usable) if usable else None
    avg_net = sum(p[2] / p[0] for p in usable) / len(usable) if usable else None
    quality = quality_of(revenue, bool(parts), True)
    gm_rate, net_rate = rates_from_sums(revenue, gross, net, quality)
    return {'revenue': revenue, 'gross_margin': gross, 'net_profit': net,
            'gm_rate': gm_rate, 'net_rate': net_rate, 'avg_gm_rate': avg_gm, 'avg_net_rate': avg_net}


def _pct(rate) -> str:
    if rate is None:
        return '不设下限'
    return f'{rate * 100:.2f}%'.replace('.00%', '%')


def advice(quadrant: str, financial: str, quality: str, thresholds: dict, strategy: dict | None,
           net_rate, gm_rate, today: date | None = None) -> dict:
    """建议只解释指标、状态和数据质量，不生成调度或拨款。"""
    today = today or date.today()
    pending = '' if thresholds.get('confirmed') else '当前阈值尚未经经营管理确认，分类和建议都只供参考，不能当作已批准标准。'
    if threshold_state(thresholds) == 'unset' and not (strategy and strategy.get('active')):
        return {'code': 'threshold_unset', 'title': '不给出投入建议',
                'text': '分类阈值还没有设完，不能判断盈利、微利或亏损，也不给出钻机布局和资源投入建议。',
                'disclaimer': DISCLAIMER}
    if quality != 'ok' and quadrant != '战略':
        return {'code': 'insufficient', 'title': '不给出投入建议',
                'text': QUALITY_LABEL[quality] + '。不计算 0%，也不给出钻机布局和资源投入建议。',
                'disclaimer': DISCLAIMER}

    overdue = ''
    if strategy and strategy.get('active') and strategy.get('review_due'):
        due = date.fromisoformat(strategy['review_due'])
        if due < today:
            overdue = f'复评期 {strategy["review_due"]} 已过，建议先复评，再决定是否继续投入。'
        limit = strategy.get('risk_limit') or ''
        base = f'基础财务分类是{financial}。战略标记不改变这个分类，也不能把它显示成没有风险。'
        if quality != 'ok':
            text = (f'已标记战略，但{QUALITY_LABEL[quality]}。不能按战略名义追加钻机或资金。'
                    f'风险约束：{limit}。复评期 {strategy["review_due"]}。{overdue}{pending}')
            return {'code': 'strategic_insufficient', 'title': '战略限额评估（数据不足）', 'text': text.strip(),
                    'disclaimer': DISCLAIMER}
        if financial == '亏损':
            title = '战略限额评估（亏损未被掩盖）'
            text = (f'{base}不建议新增钻机。只在已填写的风险约束内评估是否保留现有施工：{limit}。'
                    f'复评期 {strategy["review_due"]}。{overdue}')
        elif financial == '微利':
            title = '战略限额评估（基础为微利）'
            text = (f'{base}优先在现有钻机上提效控本，新增投入受风险约束限制：{limit}。'
                    f'复评期 {strategy["review_due"]}。{overdue}')
        elif financial == '盈利':
            title = '战略限额评估'
            text = (f'{base}可优先保障现有钻机连续施工，但不是无限投入。风险约束：{limit}。'
                    f'复评期 {strategy["review_due"]}。{overdue}')
        else:
            title = '战略限额评估（基础不可判定）'
            text = f'{base}两率不可判定时不追加投入。风险约束：{limit}。复评期 {strategy["review_due"]}。{overdue}'
        return {'code': 'strategic', 'title': title, 'text': (text + pending).strip(), 'disclaimer': DISCLAIMER}

    if financial == '盈利':
        title, text = '优先保障', (
            f'利润率 {_pct(net_rate)}、毛益率 {_pct(gm_rate)} 都达到当前盈利线'
            f'（利润率 {_pct(thresholds.get("profit_net_min"))}，毛益率 {_pct(thresholds.get("profit_gm_min"))}）。'
            '建议优先保障本区块现有钻机连续施工。不自动新增钻机，也不自动追加资金。')
    elif financial == '微利':
        missed = []
        if not _ge(net_rate, thresholds['profit_net_min']):
            missed.append('利润率未达到盈利线')
        if not _ge(gm_rate, thresholds['profit_gm_min']):
            missed.append('毛益率未达到盈利线')
        title, text = '提效控本', (
            f'两项比率达到微利线，但{"、".join(missed) or "未同时达到两条盈利线"}。'
            '建议在现有钻机上控制固定成本、提高时效，不建议据此新增钻机或追加投入。')
    else:
        missed = []
        if thresholds.get('thin_net_min') is not None and not _ge(net_rate, thresholds['thin_net_min']):
            missed.append('利润率低于微利线')
        if not _ge(gm_rate, thresholds['thin_gm_min']):
            missed.append('毛益率低于微利线')
        title, text = '限制新增 / 止损评估', (
            f'没有同时达到两条微利线（{"、".join(missed) or "见两项比率"}）。'
            '建议限制新增钻机和追加投入，由管理端评估是否停待或转出。系统不自动调整钻机，也不自动分配资金。')
    return {'code': financial, 'title': title, 'text': (text + pending).strip(), 'disclaimer': DISCLAIMER}


def _strategy_row(conn, block: str) -> dict | None:
    row = conn.execute('SELECT * FROM block_strategy_mark WHERE block=? AND active=1', (block,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data['active'] = True
    try:
        data['overdue'] = date.fromisoformat(data['review_due']) < date.today()
    except (TypeError, ValueError):
        data['overdue'] = False
    return data


def _text(value, label: str, min_len: int, max_len: int) -> str:
    text = re.sub(r'\s+', ' ', (value or '').strip())
    if len(text) < min_len:
        raise HTTPException(422, f'{label}至少 {min_len} 个字')
    if len(text) > max_len:
        raise HTTPException(422, f'{label}不能超过 {max_len} 个字')
    return text


def _review_due(value: str) -> str:
    try:
        day = date.fromisoformat((value or '').strip())
    except ValueError:
        raise HTTPException(422, '复评期须为 YYYY-MM-DD')
    if day.year < 2000 or day.year > 2100:
        raise HTTPException(422, '复评期超出可接受范围')
    return day.isoformat()


def _known_block(conn, block: str) -> str:
    name = (block or '').strip()
    if not name:
        raise HTTPException(422, '请选择区块')
    hit = conn.execute(
        'SELECT 1 FROM well WHERE block=? AND well_id NOT IN (SELECT well_id FROM well_deleted) LIMIT 1', (name,)).fetchone()
    if not hit:
        raise HTTPException(404, '没有这个区块，或区块下的井已删除')
    return name


def mark_strategy(conn, user: dict, raw: dict) -> dict:
    require_maintain(user)
    block = _known_block(conn, raw.get('block'))
    rationale = _text(raw.get('rationale'), '战略依据', 6, 500)
    risk = _text(raw.get('risk_limit'), '风险约束', 6, 200)
    compact = re.sub(r'\s+', '', risk)
    if any(word in compact for word in UNLIMITED):
        raise HTTPException(422, '风险约束不能写成无限投入或不设上限。战略区块也要有限额或复评约束')
    due = _review_due(raw.get('review_due') or '')
    now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prev = conn.execute('SELECT active FROM block_strategy_mark WHERE block=?', (block,)).fetchone()
    if prev:
        conn.execute(
            'UPDATE block_strategy_mark SET active=1, rationale=?, review_due=?, risk_limit=?, marked_by=?, marked_at=?, '
            'cleared_by=NULL, cleared_at=NULL, clear_reason=NULL WHERE block=?',
            (rationale, due, risk, user['who'], now_text, block))
    else:
        conn.execute(
            'INSERT INTO block_strategy_mark (block, active, rationale, review_due, risk_limit, marked_by, marked_at) '
            'VALUES (?, 1, ?, ?, ?, ?, ?)',
            (block, rationale, due, risk, user['who'], now_text))
    log(conn, '区块战略', block, '更新' if prev and prev['active'] else '标记', user['who'],
        f'依据：{rationale}；复评期 {due}；风险约束：{risk}')
    return _strategy_row(conn, block)


def clear_strategy(conn, user: dict, raw: dict) -> dict:
    require_maintain(user)
    block = _known_block(conn, raw.get('block'))
    reason = _text(raw.get('reason'), '取消原因', 2, 200)
    row = conn.execute('SELECT active FROM block_strategy_mark WHERE block=?', (block,)).fetchone()
    if not row or not row['active']:
        raise HTTPException(409, '该区块当前没有生效的战略标记')
    now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        'UPDATE block_strategy_mark SET active=0, cleared_by=?, cleared_at=?, clear_reason=? WHERE block=?',
        (user['who'], now_text, reason, block))
    log(conn, '区块战略', block, '取消', user['who'], reason)
    return {'ok': True, 'block': block}


def _months_or_400(conn, period: str) -> list[str]:
    if not re.fullmatch(r'\d{4}(-\d{2})?', period or ''):
        raise HTTPException(400, '期间应为 YYYY 或 YYYY-MM')
    if len(period) == 7:
        month = int(period[5:7])
        if month < 1 or month > 12:
            raise HTTPException(400, '月份不正确')
    return analysis._months(conn, period)


def _has_cost(conn, well_id: str, months: list[str]) -> bool:
    if not months:
        return False
    marks = ','.join('?' * len(months))
    return conn.execute(
        f"SELECT 1 FROM economic_event WHERE well_id=? AND period IN ({marks}) AND direction='成本' LIMIT 1",
        (well_id, *months)).fetchone() is not None


def _rig_facts(conn, well_ids: list[str], months: list[str]) -> dict:
    """队伍和钻机台月只取能对上的记录。没有记录就写未知，不用 0 冒充。钻机台数始终未知。"""
    blank = {'team_count': None, 'teams': [], 'rig_types': [], 'rig_months': None,
             'rig_count': None, 'rig_count_note': RIG_COUNT_NOTE}
    if not well_ids or not months:
        return {**blank, 'rig_months_note': '没有纳入本期汇总的井，或所选期间没有月份，施工队伍和钻机台月为未知。'}
    from .engine import period_end
    marks = ','.join('?' * len(well_ids))
    any_period = conn.execute(
        f'SELECT 1 FROM team_well_period WHERE well_id IN ({marks}) LIMIT 1', well_ids).fetchone()
    names: dict[str, str] = {}
    types: set[str] = set()
    rig_months = 0.0
    for wid in well_ids:
        for month in months:
            rig_months += analysis._ops(conn, wid, month)['rig_months']
    start, end = months[0] + '-01', period_end(months[-1])
    if any_period:
        for row in conn.execute(
                f'SELECT DISTINCT t.team_id, t.name, t.rig_type FROM team_well_period p JOIN team t ON t.team_id=p.team_id '
                f'WHERE p.well_id IN ({marks}) AND p.start_at<=? AND (p.end_at IS NULL OR p.end_at>=?)',
                (*well_ids, end, start)):
            names[row['team_id']] = row['name']
            if row['rig_type']:
                types.add(row['rig_type'])
    event_marks = ','.join('?' * len(months))
    for row in conn.execute(
            f'SELECT DISTINCT t.team_id, t.name, t.rig_type FROM economic_event e JOIN team t ON t.team_id=e.team_id '
            f'WHERE e.well_id IN ({marks}) AND e.period IN ({event_marks}) AND e.team_id IS NOT NULL',
            (*well_ids, *months)):
        names[row['team_id']] = row['name']
        if row['rig_type']:
            types.add(row['rig_type'])
    if not any_period and not names:
        return {**blank, 'rig_months_note': '这些井没有施工时段，也没有事项上的队伍，施工队伍和钻机台月为未知。'}
    if any_period and not names and rig_months == 0:
        return {'team_count': 0, 'teams': [], 'rig_types': [], 'rig_months': 0.0,
                'rig_months_note': '有施工时段记录，但没有落在所选月份，钻机台月按现有口径为 0，不是估算的钻机台数。',
                'rig_count': None, 'rig_count_note': RIG_COUNT_NOTE}
    if not any_period:
        note = '事项上能对上队伍，但没有施工时段，钻机台月为未知。队伍数只统计事项上的队伍。'
        rig_months_value = None
    else:
        note = '钻机台月 = 施工时段落在所选各月的天数 ÷ 当月天数，与经营总览、费用导出口径相同。没有交上的月份不加台月。'
        rig_months_value = rig_months
    return {'team_count': len(names), 'teams': sorted(names.values()), 'rig_types': sorted(types),
            'rig_months': rig_months_value, 'rig_months_note': note,
            'rig_count': None, 'rig_count_note': RIG_COUNT_NOTE}


def build_map(conn, user: dict, period: str) -> dict:
    require_read(user)
    months = _months_or_400(conn, period)
    thresholds = load_thresholds(conn)
    wells = auth.visible_wells(conn, user)
    grouped: dict[str, list] = {}
    for wid in wells:
        info = analysis.well_info(conn, wid, months[-1] if months else (period if len(period) == 7 else period + '-01'))
        block = info['block']
        revenue = fixed = variable = total_cost = gross = net = footage = 0
        included = []
        has_events = False
        has_cost = _has_cost(conn, wid, months)
        for month in months:
            if not conn.execute('SELECT 1 FROM economic_event WHERE well_id=? AND period=? LIMIT 1', (wid, month)).fetchone():
                continue
            has_events = True
            kpi = analysis.compute(conn, user, wid, month)['kpi']
            revenue += kpi['revenue']
            fixed += kpi['fixed']
            variable += kpi['variable']
            total_cost += kpi['total_cost']
            gross += kpi['gross_margin']
            net += kpi['net_profit']
            foot = analysis.depth_footage(conn, wid, month)
            footage += foot
            included.append(month)
        wq = quality_of(revenue, has_events, has_cost)
        gm_rate, net_rate = rates_from_sums(revenue, gross, net, wq)
        grouped.setdefault(block, []).append({
            'well_id': wid, 'team': info['team'], 'rig_type': info['rig_type'] if info['rig_type'] not in (None, '', '—') else None,
            'status': info['status'], 'has_events': has_events, 'revenue': revenue, 'fixed': fixed, 'variable': variable,
            'total_cost': total_cost, 'gross_margin': gross, 'net_profit': net, 'footage': footage,
            'gm_rate': gm_rate, 'net_rate': net_rate, 'quality': wq, 'months': included,
        })

    blocks = []
    for name in sorted(grouped):
        rows = grouped[name]
        active = [r for r in rows if r['has_events']]
        idle = [r for r in rows if not r['has_events']]
        revenue = sum(r['revenue'] for r in active)
        fixed = sum(r['fixed'] for r in active)
        variable = sum(r['variable'] for r in active)
        total_cost = sum(r['total_cost'] for r in active)
        gross = sum(r['gross_margin'] for r in active)
        net = sum(r['net_profit'] for r in active)
        footage = sum(r['footage'] for r in active)
        # 成本是否记录过，看井上有没有成本类事项，不看金额是不是 0
        has_cost = any(_has_cost(conn, r['well_id'], r['months']) for r in active) if active else False
        quality = quality_of(revenue, bool(active), has_cost)
        gm_rate, net_rate = rates_from_sums(revenue, gross, net, quality)
        financial = classify(net_rate, gm_rate, quality, thresholds)
        strategy = _strategy_row(conn, name)
        quadrant = '战略' if strategy else financial
        label_prefix = '' if thresholds.get('confirmed') or financial == '不可判定' else '参考·'
        rig = _rig_facts(conn, [r['well_id'] for r in active], months)
        item = {
            'block': name,
            'revenue': revenue, 'fixed': fixed, 'variable': variable, 'total_cost': total_cost,
            'gross_margin': gross, 'net_profit': net, 'gm_rate': gm_rate, 'net_rate': net_rate,
            'footage': footage if active else None,
            'footage_note': '进尺与经营总览相同：有日报的按井深差，没有日报的历史井按实际井深计入导入月。没有纳入汇总的井不计进尺。',
            'quality': quality, 'quality_label': QUALITY_LABEL[quality],
            'financial_class': financial,
            'financial_label': label_prefix + financial,
            'quadrant': quadrant,
            'quadrant_label': quadrant if quadrant == '战略' or financial == '不可判定' or thresholds.get('confirmed') else '参考·' + quadrant,
            'loss_visible': financial == '亏损',
            'strategy': strategy,
            'well_count': len(active),
            'wells': [{k: r[k] for k in ('well_id', 'team', 'rig_type', 'status', 'revenue', 'gross_margin', 'net_profit',
                                         'gm_rate', 'net_rate', 'quality')} for r in active],
            'idle_wells': [{'well_id': r['well_id'], 'team': r['team'], 'rig_type': r['rig_type']} for r in idle],
            'budget': None, 'budget_note': BUDGET_NOTE,
            **rig,
            'advice': advice(quadrant, financial, quality, thresholds, strategy, net_rate, gm_rate),
        }
        blocks.append(item)

    counts = {k: 0 for k in ('盈利', '微利', '亏损', '战略', '不可判定')}
    for item in blocks:
        counts[item['quadrant']] = counts.get(item['quadrant'], 0) + 1
    return {
        'period': period, 'months': months, 'scope': user['scope_label'], 'role': user['role'],
        'can_maintain': user['role'] in MAINTAIN_ROLES,
        'scope_note': SCOPE_NOTE.get(user['role'], ''),
        'thresholds': {k: thresholds.get(k) for k in (
            'profit_net_min', 'profit_gm_min', 'thin_net_min', 'thin_gm_min', 'confirmed', 'state', 'state_label',
            'official', 'reference', 'note', 'updated_by', 'updated_at', 'confirmed_by', 'confirmed_at')},
        'formula': FORMULA,
        'classes': [
            {'id': '盈利', 'rule': '利润率 ≥ 盈利线，并且毛益率 ≥ 盈利线。两项都要达到。'},
            {'id': '微利', 'rule': '没有达到盈利，但利润率 ≥ 微利线（若未设下限则不看这条）并且毛益率 ≥ 微利线。'},
            {'id': '亏损', 'rule': '利润率或毛益率至少有一项低于对应的微利线。只看毛益率达不到，只看利润率也达不到。'},
            {'id': '战略', 'rule': '管理端或系统管理员按区块手工标记，并填写依据、复评期和风险约束。不是利润率自动算出来的。基础财务分类仍然显示。'},
            {'id': '不可判定', 'rule': '形象收入 ≤ 0、没有经济事项、没有成本事项，或阈值还没设完。不显示 0%，不判成盈利。'},
        ],
        'blocks': blocks,
        'counts': counts,
        'disclaimer': DISCLAIMER,
    }
