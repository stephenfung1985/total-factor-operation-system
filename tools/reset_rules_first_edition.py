"""清理规则库与测试数据，并把《首版结算依据》写成依据文件和已发布规则。

用法：python3 tools/reset_rules_first_edition.py 数据库路径            只演练：打印将删除 / 写入的数量，不改库
      python3 tools/reset_rules_first_edition.py 数据库路径 --apply    先备份，再在一个事务里执行

清理：规则、规则条件、条款、依据文件、AI 任务、回算记录，以及示例井 W001–W004 的全部数据
（事项、事实、日报、基础信息、队伍时段、井、示例队伍 t1–t4、演示账号 u_fin / u_team）。
历史导入的 505 口井和账号（fengxuecheng、jinliuchao、各钻井队账号、test）不动。
写入：docs/首版结算依据-历史经营数据反算.md 对应的依据文件，及其中 3 条已发布规则。
已上传到 docs 目录的原始文件（合同 PDF / Word）保留在磁盘上，重新上传解析即可。
"""
import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
from app.db import log, now  # noqa: E402

DOC_FILE = ROOT / 'docs' / '首版结算依据-历史经营数据反算.md'
DEMO_WELLS = ('W001', 'W002', 'W003', 'W004')
DEMO_TEAMS = ('t1', 't2', 't3', 't4')
DEMO_USERS = ('u_fin', 'u_team')
OPERATOR = '系统 · 首版结算依据'
VALID_FROM = '2025-01-01'
BASIS = '《首版结算依据：历史经营数据反算》'

# (规则编码, 名称, 科目, 条款号, 事实类型, 计价方式, 基数科目, 计算顺序, 单价, 单位, 粒度, 条件, 条款原文)
RULES = [
    ('RL-C2-3-31', '税金（形象收入 × 0.3%）', 'C2-3-31', '2.1', None, '比例', 'R02', 3, 0.003, '元', '按期间', [],
     '31.税金 = 形象收入 × 0.3%。长庆 321/321、西北 80/80、大港 93/96 口井金额与此完全一致。'),
    ('RL-C2-3-30', '科研计提（形象收入 × 0.2%）', 'C2-3-30', '2.2', None, '比例', 'R02', 3, 0.002, '元', '按期间', [],
     '30.科研计提 = 形象收入 × 0.2%。497 口有形象收入的井全部一致；与成本项目树标注的 2% 不同，以历史数据为准，待制度文本确认。'),
    ('RL-C2-1-08-01', '钻具摊销（长庆，按进尺）', 'C2-1-08-01', '2.3', '进尺', '单价', None, 2, 63.0, '米', '逐日',
     [('市场', '等于', '长庆')],
     '（1）钻具摊销 = 进尺 × 63 元/米。长庆 305/321 口井一致，另有 13 口为 104 元/米；大港、西北档位不统一，暂不入库。'),
]


def counts(conn) -> dict:
    ph = ','.join('?' * len(DEMO_WELLS))
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    return {
        '规则': q('SELECT COUNT(*) FROM economic_rule'), '条款': q('SELECT COUNT(*) FROM clause'),
        '依据文件': q('SELECT COUNT(*) FROM source_doc'), 'AI任务': q('SELECT COUNT(*) FROM ai_task'),
        '规则测试': q('SELECT COUNT(*) FROM rule_test'),
        '示例井经济事项': q(f'SELECT COUNT(*) FROM economic_event WHERE well_id IN ({ph})', *DEMO_WELLS),
        '示例井日报': q(f'SELECT COUNT(*) FROM daily_report WHERE well_id IN ({ph})', *DEMO_WELLS),
        '示例井事实': q(f'SELECT COUNT(*) FROM business_fact WHERE well_id IN ({ph})', *DEMO_WELLS),
        '示例井': q(f'SELECT COUNT(*) FROM well WHERE well_id IN ({ph})', *DEMO_WELLS),
        '历史导入事项（保留）': q("SELECT COUNT(*) FROM economic_event WHERE origin_type='历史导入' "
                          f'AND well_id NOT IN ({ph})', *DEMO_WELLS),
    }


def check(conn) -> None:
    ph = ','.join('?' * len(DEMO_WELLS))
    n = conn.execute(f'SELECT COUNT(*) FROM economic_event WHERE well_id NOT IN ({ph}) AND origin_type!=\'历史导入\'',
                     DEMO_WELLS).fetchone()[0]
    if n:
        sys.exit(f'中止：示例井之外还有 {n} 笔规则计算 / 分摊事项，删除规则会使它们失去出处，请先人工处理')
    n = conn.execute(f'SELECT COUNT(*) FROM team_well_period WHERE team_id IN ({",".join("?" * len(DEMO_TEAMS))}) '
                     f'AND well_id NOT IN ({ph})', DEMO_TEAMS + DEMO_WELLS).fetchone()[0]
    if n:
        sys.exit('中止：示例队伍 t1–t4 在示例井之外还有施工时段')


def reset(conn) -> None:
    ph = ','.join('?' * len(DEMO_WELLS))
    triggers = {r[0]: r[1] for r in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name IN ('trg_event_no_delete', 'trg_rule_published_frozen')")}
    for name in triggers:
        conn.execute(f'DROP TRIGGER {name}')
    for sql in (
        f'DELETE FROM economic_event WHERE well_id IN ({ph})',
        f'DELETE FROM validation_alert WHERE report_id IN (SELECT report_id FROM daily_report WHERE well_id IN ({ph}))',
        f'DELETE FROM business_fact WHERE well_id IN ({ph})',
        f'DELETE FROM daily_report WHERE well_id IN ({ph})',
        f'DELETE FROM well_milestone WHERE well_id IN ({ph})',
        f'DELETE FROM team_well_period WHERE well_id IN ({ph})',
        f'DELETE FROM well_info_report WHERE well_id IN ({ph})',
        f'DELETE FROM well WHERE well_id IN ({ph})',
    ):
        conn.execute(sql, DEMO_WELLS)
    conn.execute(f'DELETE FROM team WHERE team_id IN ({",".join("?" * len(DEMO_TEAMS))})', DEMO_TEAMS)
    marks = ','.join('?' * len(DEMO_USERS))
    conn.execute(f'DELETE FROM app_session WHERE user_id IN ({marks}) OR acting_user_id IN ({marks})', DEMO_USERS * 2)
    conn.execute(f'DELETE FROM app_user WHERE user_id IN ({marks})', DEMO_USERS)
    for t in ('rule_test', 'rule_condition', 'economic_rule', 'clause', 'ai_task', 'source_doc'):
        conn.execute(f'DELETE FROM {t}')
    conn.execute("DELETE FROM calc_batch WHERE trigger IN ('初始化', '规则新版本发布', '规则回滚') AND batch_id NOT IN "
                 '(SELECT DISTINCT batch_id FROM economic_event WHERE batch_id IS NOT NULL)')
    for sql in triggers.values():
        conn.execute(sql)


def install_basis(conn, docs_dir: Path) -> None:
    data = DOC_FILE.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / f'{digest}.md'
    path.write_bytes(data)
    doc_id = conn.execute(
        'INSERT INTO source_doc (doc_type, doc_no, doc_name, counterparty, file_hash, parse_status, file_name, file_path, '
        "full_text, page_count, valid_from, uploaded_by, uploaded_at) VALUES ('内部结算价', NULL, ?, NULL, ?, '已解析', ?,?,?,?,?,?,?)",
        ('首版结算依据：历史经营数据反算', 'sha256:' + digest, DOC_FILE.name, str(path), data.decode('utf-8'), 1, VALID_FROM,
         OPERATOR, now())).lastrowid
    log(conn, '依据文件', doc_id, '上传', OPERATOR, f'{DOC_FILE.name}，由 2025-01 至 2026-09 历史经营数据反算')
    for code, name, item, clause_no, fact, method, base, order, price, unit, gran, conds, hit in RULES:
        clause_id = conn.execute('INSERT INTO clause (doc_id, clause_no, page_no, text_pre, text_hit, text_post) VALUES (?,?,?,?,?,?)',
                                 (doc_id, clause_no, '1', '', hit, '')).lastrowid
        rid = conn.execute(
            'INSERT INTO economic_rule (rule_code, version, name, item_code, clause_id, fact_type, pricing_method, base_item_code, '
            "calc_order, unit_price, unit, calc_params, combine_mode, granularity, priority, valid_from, status, origin, "
            "field_levels, confirmed_by, confirmed_at, published_at, change_basis) VALUES (?,1,?,?,?,?,?,?,?,?,?,'{}','互斥',?,0,?,"
            "'已发布','历史反算','{}',?,?,?,?)",
            (code, name, item, clause_id, fact, method, base, order, price, unit, gran, VALID_FROM, OPERATOR, now(), now(),
             BASIS + clause_no + '条')).lastrowid
        for dim, op, val in conds:
            conn.execute('INSERT INTO rule_condition (rule_id, dimension, operator, value) VALUES (?,?,?,?)', (rid, dim, op, val))
        log(conn, '规则', rid, '发布', OPERATOR, f'生效日 {VALID_FROM}；变化依据：{BASIS}{clause_no}条')
    log(conn, '规则', 0, '清理', OPERATOR, '清空示例规则与示例井数据，改为首版结算依据 3 条规则')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('db')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    db = Path(args.db).expanduser()
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    check(conn)
    before = counts(conn)
    print('当前：', before)
    if not args.apply:
        print('演练结束，未改库。加 --apply 执行。')
        return
    backup = db.parent / 'backups' / f'tfos-{datetime.now():%Y%m%d-%H%M%S}-before-rules-reset.db'
    backup.parent.mkdir(exist_ok=True)
    dst = sqlite3.connect(backup)
    conn.backup(dst)
    dst.close()
    print('已备份：', backup)
    conn.isolation_level = None
    conn.execute('BEGIN IMMEDIATE')
    try:
        reset(conn)
        install_basis(conn, db.parent / 'docs')
        bad = conn.execute('PRAGMA foreign_key_check').fetchall()
        if bad:
            raise RuntimeError(f'外键检查未通过：{[tuple(b) for b in bad[:5]]}')
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise
    print('完成后：', counts(conn))
    print('完整性：', conn.execute('PRAGMA integrity_check').fetchone()[0])


if __name__ == '__main__':
    main()
