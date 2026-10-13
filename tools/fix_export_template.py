"""一次性迁移脚本：把旧版《单井经营情况导出模板》改为新口径（已对仓库里的模板执行过，无需再跑）。

- 删除“4.使用权资产租赁”及 5 个子项共 6 行，后续固定成本项目重新编号
- 安全计提、科研计提、税金计入“（三）其他直接费用”和“二、变动成本”合计
- 重算毛益额、毛益率；总成本、净利润不变

用法：python3 tools/fix_export_template.py 旧模板.xlsx 新模板.xlsx
"""
import sys, openpyxl
src, dst = sys.argv[1], sys.argv[2]
wb = openpyxl.load_workbook(src); ws = wb.active
assert ws['A31'].value.startswith('4.使用权资产租赁') and ws['A36'].value.startswith('（5）其他租赁费')
assert all(float(ws.cell(r,2).value or 0) == 0 for r in range(31,37))
has_formula = any(isinstance(c.value, str) and c.value.startswith('=') for row in ws.iter_rows() for c in row)
print('formulas:', has_formula)
ws.delete_rows(31, 6)
# renumber fixed items after the removed one
for r in range(1, ws.max_row+1):
    v = ws.cell(r,1).value
    if v == '5.项目部运行费用': ws.cell(r,1).value = '4.项目部运行费用'
    if v == '6.制造费': ws.cell(r,1).value = '5.制造费'
rows = {ws.cell(r,1).value: r for r in range(1, ws.max_row+1)}
val = lambda k: float(ws.cell(rows[k],2).value or 0)
tiqu = val('29.安全计提') + val('30.科研计提') + val('31.税金')
other = val('（三）其他直接费用') + tiqu
var = val('（一）直接材料费') + val('（二）机械使用费') + other
fixed = val('一、固定成本')
total = fixed + var
rev = val('形象收入（元）')
assert abs(total - val('总成本（元）')) < 0.01, (total, val('总成本（元）'))
gm = rev - var; net = rev - total
set_ = lambda k, x: ws.cell(rows[k], 2).__setattr__('value', x)
set_('（三）其他直接费用', other); set_('二、变动成本', var)
set_('（二）变动成本（元）', var); set_('毛益额（元）', gm)
set_('毛益率（%）', gm / rev if rev else 0)
print('rows now', ws.max_row, 'var', round(var,2), 'gm', round(gm,2), 'net', round(net,2), 'stated net', val('净利润（元）'))
wb.save(dst)
