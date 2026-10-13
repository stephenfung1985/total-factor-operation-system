"""Excel 导出与导入。

- 单井经营情况：在《单井经营情况导出模板》上逐行填 B 列，行序与模板一致。
- 成本日报模板：按井号和日期区间导出，每天一个工作表；表头和单位锁定，隐藏列带行键和模板版本号。
  导入只存为草稿，校验与线上一致，不允许导入即提交。
"""
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.worksheet.datavalidation import DataValidation

from . import analysis, report_lines, reports

TEMPLATE = Path(__file__).resolve().parent / 'data' / 'export_template.xlsx'
REPORT_TPL_VERSION = 'DR-2'


def well_sheet(conn, well_id: str, period: str) -> bytes:
    rows = analysis.export_rows(conn, well_id, period)
    wb = openpyxl.load_workbook(TEMPLATE)
    ws = wb.active
    if ws.max_row != len(rows):
        raise ValueError(f'导出模板行数 {ws.max_row} 与系统成本树 {len(rows)} 行不一致，请先同步模板')
    for i, r in enumerate(rows, start=1):
        label = str(ws.cell(i, 1).value or '').strip()
        if label != r['k']:
            raise ValueError(f'导出模板第 {i} 行“{label}”与系统项目“{r["k"]}”不一致')
        ws.cell(i, 2).value = r['v']
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _days(start: str, end: str) -> list[str]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if b < a or (b - a).days > 31:
        raise reports.ReportError('日期区间不合法（最多 31 天）')
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def report_template(conn, well_id: str, team_id: str, start: str, end: str) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    head = Font(bold=True)
    lock = PatternFill('solid', fgColor='F2F2F7')
    for d in _days(start, end):
        rep = reports.find_or_new(conn, well_id, d, team_id)
        h = rep['header']
        ws = wb.create_sheet(d)
        ws.column_dimensions['A'].width = 24
        ws.column_dimensions['B'].width = 12
        ws.column_dimensions['C'].width = 8
        ws.column_dimensions['D'].width = 26
        ws.column_dimensions['E'].hidden = True
        meta = [('井号', h['well_id']), ('日期', d), ('队伍', h['team']), ('区块', h['block']), ('井型', h['well_type']),
                ('当前开次', h['phase'])]
        for i, (k, v) in enumerate(meta, start=1):
            ws.cell(i, 1, k).font = head
            ws.cell(i, 2, v).fill = lock
        ws.cell(1, 5, f'template={REPORT_TPL_VERSION}')
        r = len(meta) + 2
        ws.cell(r, 1, '项目').font = head
        ws.cell(r, 2, '数量').font = head
        ws.cell(r, 3, '单位').font = head
        ws.cell(r, 4, '签证单号 / 设备编号').font = head
        r += 1
        sec_title = {x['id']: x['title'] for x in report_lines.SECTIONS}
        lines = [('§', '一、' + sec_title['drill'], None, None, None),
                 ('depth_start', '起始井深', rep['depth_start'], '米', None), ('depth_end', '结束井深', rep['depth_end'], '米', None)]
        lines += [(f'hour:{n}', f'工况 · {n}', rep['hours'].get(n, 0), '小时', None) for n in reports.WORK_CONDITIONS]
        cn = '二三四五六'
        for i, sec in enumerate(x['id'] for x in report_lines.SECTIONS[1:]):
            rows = [ld for ld in rep['line_defs'] if ld['section'] == sec and (ld['show'] or ld['key'] in rep['lines'])]
            if not rows:
                continue
            lines.append(('§', f'{cn[i]}、{sec_title[sec]}', None, None, None))
            lines += [(f"line:{ld['key']}", f"{ld['group']} · {ld['label']}", rep['lines'].get(ld['key'], 0), ld['unit'],
                       rep['notes'].get(ld['key'], '')) for ld in rows]
        for key, label, val, unit, note in lines:
            if key == '§':
                ws.cell(r, 1, label).font = head
                r += 1
                continue
            ws.cell(r, 1, label).fill = lock
            c = ws.cell(r, 2, val)
            c.protection = Protection(locked=False)
            c.alignment = Alignment(horizontal='right')
            ws.cell(r, 3, unit).fill = lock
            ws.cell(r, 4, note).protection = Protection(locked=False)
            ws.cell(r, 5, key)
            r += 1
        dv = DataValidation(type='decimal', operator='greaterThanOrEqual', formula1='0', showErrorMessage=True,
                            error='请输入不小于 0 的数字')
        ws.add_data_validation(dv)
        dv.add(f'B{len(meta) + 3}:B{r - 1}')
        ws.protection.sheet = True
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_reports(conn, data: bytes, user: dict) -> list[dict]:
    """解析 → 与线上相同的校验 → 存为草稿。已提交 / 已审核的日报跳过，须走更正流程。"""
    wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
    out = []
    for ws in wb.worksheets:
        if str(ws.cell(1, 5).value or '') != f'template={REPORT_TPL_VERSION}':
            out.append({'sheet': ws.title, 'ok': False, 'message': '不是本系统导出的日报模板，或模板版本已过期'})
            continue
        well_id, d = str(ws.cell(1, 2).value), str(ws.cell(2, 2).value)[:10]
        body = {'well_id': well_id, 'report_date': d, 'team_id': user['team_id'], 'hours': {}, 'lines': {}, 'notes': {}}
        keys = set()
        for row in range(1, ws.max_row + 1):
            key, val = ws.cell(row, 5).value, ws.cell(row, 2).value
            if not key or not isinstance(key, str) or key.startswith('template='):
                continue
            keys.add(key)
            try:
                v = float(val or 0)
            except (TypeError, ValueError):
                v = 0.0
            if key in ('depth_start', 'depth_end'):
                body[key] = v
            elif key.startswith('hour:'):
                body['hours'][key[5:]] = v
            elif key.startswith('line:'):
                body['lines'][key[5:]] = v
                note = ws.cell(row, 4).value
                if note not in (None, ''):
                    body['notes'][key[5:]] = str(note).strip()
        defs = reports.line_defs(conn, well_id, d, user['team_id'])
        missing = [ld['label'] for ld in defs if ld['show'] and ld['priced'] and f"line:{ld['key']}" not in keys]
        try:
            rid = reports.save_draft(conn, body, user)
        except reports.ReportError as e:
            out.append({'sheet': ws.title, 'ok': False, 'message': str(e)})
            continue
        rep = reports.get_report(conn, rid)
        msg = '已存为草稿'
        if missing:
            msg += f"；模板导出后规则库新增了：{'、'.join(missing)}，请补填"
        out.append({'sheet': ws.title, 'ok': True, 'report_id': rid, 'message': msg,
                    'checks': rep['checks']})
    return out


def well_info_sheet(conn) -> bytes:
    """单井基础信息汇总：列与《单井基础信息表》一致，一井一行。"""
    from . import wellinfo
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '单井基础信息表'
    ws.append(wellinfo.EXCEL_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for rec in wellinfo.summary(conn):
        ws.append(wellinfo.excel_row(rec))
    date_cols = {i + 1 for i, h in enumerate(wellinfo.EXCEL_HEADERS) if h.endswith('时间') or h.endswith('完钻') or h.endswith('日期')}
    num_cols = {i + 1 for i, h in enumerate(wellinfo.EXCEL_HEADERS) if h.endswith('（天）')}
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if c.column in date_cols:
                c.number_format = 'yyyy/mm/dd hh:mm'
            elif c.column in num_cols:
                c.number_format = '0.00'
            elif c.column == 13:
                c.number_format = '#,##0.00'
    for i, h in enumerate(wellinfo.EXCEL_HEADERS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 17 if i in date_cols else max(9, len(h) * 2)
    ws.freeze_panes = 'B2'
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_well_info(data: bytes) -> list[dict]:
    """读取《单井基础信息表》格式：按表头名称取列（列序可不同），跳过空行。"""
    from . import wellinfo
    try:
        wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
    except Exception as e:  # noqa: BLE001 - 非 xlsx 文件
        raise wellinfo.InfoError(f'无法读取文件：{e}') from e
    for ws in wb.worksheets:
        header = [str(c.value).strip().replace(' ', '') if c.value is not None else '' for c in ws[1]]
        if '井号' not in header:
            continue
        missing = [h for h in wellinfo.REQUIRED_HEADERS if h not in header]
        if missing:
            raise wellinfo.InfoError('缺少列：' + '、'.join(missing))
        idx = {h: header.index(h) for h in wellinfo.EXCEL_HEADERS if h in header}
        out = []
        for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(v not in (None, '') for v in row):
                continue
            out.append({'row': n, 'values': {h: row[i] if i < len(row) else None for h, i in idx.items()}})
        return out
    raise wellinfo.InfoError('没有找到含“井号”列的工作表，请使用《单井基础信息表》格式')
