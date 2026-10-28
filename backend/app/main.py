"""全要素经营管理系统 v3.0 · API

启动：uvicorn app.main:app --reload（在 backend 目录下）
前端构建产物 frontend/dist 存在时，同一端口直接提供页面。
"""
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import accounts, ai, aiparse, allocation, analysis, auth, costdetail, engine, excel, master, reports, results_import, rules, wellinfo
from .db import init_db, log, rows, transaction

def docs_kwargs() -> dict:
    """对外服务模式关闭 /docs、/redoc、/openapi.json，不向公网暴露接口清单。"""
    return {'docs_url': None, 'redoc_url': None, 'openapi_url': None} if accounts.is_production() else {}


app = FastAPI(title='全要素经营管理系统 v3.0', version='3.0.0', **docs_kwargs())
User = Depends(auth.current_user)
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


MAX_UPLOAD = int(float(os.environ.get('TFOS_MAX_UPLOAD_MB', '10')) * 1024 * 1024)   # 上传文件（Excel 导入）大小上限


@app.middleware('http')
async def _limit_body(request, call_next):
    """请求体声明超限时在解析前直接拒绝，避免超大文件占满内存和磁盘。留 1MB 给表单边界等开销。"""
    from fastapi.responses import JSONResponse
    try:
        size = int(request.headers.get('content-length') or 0)
    except ValueError:
        size = 0
    if size > MAX_UPLOAD + 1024 * 1024:
        return JSONResponse(status_code=413, content={'detail': f'文件过大，上限 {MAX_UPLOAD // 1024 // 1024} MB'})
    return await call_next(request)


async def _read_upload(file: UploadFile) -> bytes:
    """读取上传文件；没有 Content-Length（分块上传）时也按上限截断拒绝。"""
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f'文件过大，上限 {MAX_UPLOAD // 1024 // 1024} MB')
    return data


@app.on_event('startup')
def _startup() -> None:
    init_db()


@app.exception_handler(reports.ReportError)
def _report_error(_req, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={'detail': str(exc)})


def _xlsx(data: bytes, filename: str) -> Response:
    return Response(data, media_type=XLSX, headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}"})


# ---------- 会话 ----------

class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.exception_handler(accounts.AuthError)
def _auth_error(_req, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400, content={'detail': str(exc)})


def _me(user: dict, conn=None) -> dict:
    out = {**accounts.public(user), 'role_name': auth.ROLE_NAMES[user['role']], 'nav': auth.NAV[user['role']]}
    if user.get('debug'):
        out.update(debug=True, login_username=user['login_username'], must_change=0,
                   identities=[{**d, 'role_name': auth.ROLE_NAMES[d['role']]} for d in accounts.demo_identities(conn)])
    return out


def _token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, '请先登录')
    return authorization[7:].strip()


@app.post('/api/auth/login')
def login(body: LoginBody):
    with transaction() as c:
        token, _ = accounts.login(c, body.username, body.password)
        user = accounts.user_by_token(c, token)
        log(c, '账号', user.get('login_user_id', user['user_id']), '登录', user['who'])
        return {'token': token, 'user': _me(user, c)}


@app.post('/api/auth/logout')
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith('Bearer '):
        with transaction() as c:
            accounts.logout(c, authorization[7:].strip())
    return {'ok': True}


@app.post('/api/auth/password')
def change_password(body: PasswordBody, user=User):
    with transaction() as c:
        uid = user.get('login_user_id', user['user_id'])   # 调试账号改的是登录账号自己的密码
        accounts.change_password(c, uid, body.old_password, body.new_password)
        log(c, '账号', uid, '修改密码', user['who'])
    return {'ok': True}


class SwitchBody(BaseModel):
    user_id: str


@app.post('/api/auth/switch')
def switch_identity(body: SwitchBody, authorization: str | None = Header(default=None)):
    """调试账号切换演示身份（管理端 / 填报端）。"""
    token = _token(authorization)
    with transaction() as c:
        accounts.switch_identity(c, token, body.user_id)
        user = accounts.user_by_token(c, token)
        log(c, '账号', user['login_user_id'], '切换调试身份', user['who'])
        return _me(user, c)


@app.get('/api/health')
def health():
    return {'ok': True}


@app.get('/api/me')
def me(user=User):
    with transaction() as c:
        return _me(user, c)


# ---------- 经营总览与下钻 ----------

@app.get('/api/periods')
def periods(user=User):
    with transaction() as c:
        return analysis.periods(c)


@app.get('/api/overview')
def overview(period: str, user=User):
    """period 为 YYYY-MM（单月）或 YYYY（全年累计）。"""
    with transaction() as c:
        return analysis.overview(c, user, period)


@app.get('/api/overview/trend')
def overview_trend(user=User):
    with transaction() as c:
        return analysis.trend(c, user)


@app.get('/api/wells/{well_id}/result')
def well_result(well_id: str, period: str, user=User):
    with transaction() as c:
        return analysis.well_result(c, user, well_id, period)


@app.get('/api/wells/{well_id}/items/{code}/events')
def item_events(well_id: str, code: str, period: str, user=User):
    with transaction() as c:
        return analysis.item_events(c, user, well_id, code, period)


@app.get('/api/events/{event_id:int}/trace')
def trace(event_id: int, user=User):
    with transaction() as c:
        return analysis.trace(c, user, event_id)


@app.get('/api/benchmark')
def benchmark(period: list[str] = Query(['all']), dims: str = 'market', market: list[str] = Query([]), block: list[str] = Query([]),
              team: list[str] = Query([]), well: list[str] = Query([]), user=User):
    """经营对标：dims 为分组维度（market / block / team / well，逗号分隔）；
    market / block / team / well 可重复传参，选定参与对比的具体对象；period 可重复传参多选，元素为 YYYY-MM、YYYY（整年）或 all。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return analysis.benchmark(c, period, [d.strip() for d in dims.split(',') if d.strip()],
                                  {'market': market, 'block': block, 'team': team, 'well': well})


# ---------- 费用明细（单项成本对比） ----------

def _codes(items: str) -> list[str]:
    return [c.strip() for c in items.split(',') if c.strip()]


@app.get('/api/cost-detail/options')
def cost_detail_options(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return costdetail.options(c, user)


@app.get('/api/cost-detail')
def cost_detail(by: str = 'period', items: str = '', start: str = '', end: str = '', user=User):
    """by：period / market / block / team；items：科目编码（逗号分隔，含下级），留空为全部可见科目；start / end：YYYY-MM。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return costdetail.matrix(c, user, by, _codes(items), start, end)


@app.get('/api/cost-detail/export.xlsx')
def cost_detail_export(by: str = 'period,market,block,team', items: str = '', start: str = '', end: str = '',
                       detail: bool = True, unit: bool = True, user=User):
    """导出：by 可多选，每个维度一个金额工作表；unit=true 再附米成本、单位成本（元/台月）两张表；detail=true 另附“事项明细”工作表便于透视分析。"""
    auth.require(user, 'finance')
    bys = [b.strip() for b in by.split(',') if b.strip()]
    bad = [b for b in bys if b not in costdetail.DIMS]
    if bad:
        raise HTTPException(400, '维度应为 period / market / block / team 之一')
    with transaction() as c:
        start, end = costdetail._check_range(start, end)
        data = costdetail.workbook(c, user, _codes(items), start, end, bys, detail, unit)
        log(c, '导出', 'cost-detail', '费用明细导出', user['who'],
            f"维度 {','.join(bys) or '全部'}；科目 {items or '全部'}；期间 {start or '起'} ~ {end or '今'}")
    return _xlsx(data, f"费用明细_{user['scope_label']}_{start or '全部'}_{end or '全部'}.xlsx")


@app.exception_handler(results_import.ImportError_)
def _import_error(_req, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={'detail': str(exc)})


@app.get('/api/results/template.xlsx')
def results_template(user=User):
    auth.require(user, 'finance')
    return _xlsx(results_import.blank_template(), '单井经营情况导入模板.xlsx')


@app.post('/api/results/import')
async def results_import_(commit: bool = False, file: list[UploadFile] = File(...), user=User):
    """单井经营结果按模板导入（可一次多个文件）：commit=false 只预览；未经基层队登记的井备注“历史数据井”。"""
    auth.require(user, 'finance')
    files = [(f.filename or '导入文件.xlsx', await _read_upload(f)) for f in file]
    with transaction() as c:
        return results_import.run(c, files, user, commit)


@app.delete('/api/wells/{well_id}')
def delete_well(well_id: str, user=User):
    """手工删除一口井（正钻或完成）：冲销该井全部有效经济事项、从各页面隐藏，可追溯；历史数据井再次导入时恢复。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return analysis.delete_well(c, well_id, user)


def _can_export(c, user, well_id):
    auth.require(user, 'finance', 'team')
    auth.check_well(c, user, well_id)


@app.get('/api/wells/{well_id}/export/preview')
def export_preview(well_id: str, period: str, user=User):
    with transaction() as c:
        _can_export(c, user, well_id)
        return analysis.export_rows(c, well_id, period)


@app.get('/api/wells/{well_id}/export.xlsx')
def export_xlsx(well_id: str, period: str, user=User):
    with transaction() as c:
        _can_export(c, user, well_id)
        data = excel.well_sheet(c, well_id, period)
    return _xlsx(data, f'单井经营情况_{well_id}_{period}.xlsx')


# ---------- 成本日报 ----------

class ReportBody(BaseModel):
    well_id: str
    report_date: str
    depth_start: float = 0
    depth_end: float = 0
    hours: dict[str, float] = {}
    lines: dict[str, float] = {}
    notes: dict[str, str] = {}


class Comment(BaseModel):
    comment: str | None = None


def _team_ctx(c, user, well_id):
    auth.require(user, 'team')
    auth.check_well(c, user, well_id)


@app.get('/api/reports/catalog')
def report_catalog(user=User):
    """报量项目字典：分段、工况、报量项目，以及不由基层填报的成本项目及原因。"""
    with transaction() as c:
        return reports.catalog(c)


@app.get('/api/reports/wells')
def report_wells(user=User):
    auth.require(user, 'team')
    with transaction() as c:
        return rows(c.execute(
            'SELECT DISTINCT w.well_id, w.block, w.well_type, w.status FROM well w JOIN team_well_period p ON p.well_id=w.well_id '
            "WHERE p.team_id=? AND w.status IN ('计划','正钻','完钻') AND w.well_id NOT IN (SELECT well_id FROM well_deleted) "
            "ORDER BY w.well_id", (user['team_id'],)))


@app.get('/api/reports/mine')
def report_mine(user=User):
    """本队最近的日报，草稿和驳回的排在前面作为待办。"""
    auth.require(user, 'team')
    with transaction() as c:
        return rows(c.execute(
            "SELECT report_id, well_id, report_date, version, status, review_comment FROM daily_report WHERE team_id=? "
            "AND status!='已被更正' ORDER BY CASE WHEN status IN ('草稿','驳回') THEN 0 ELSE 1 END, report_date DESC LIMIT 12",
            (user['team_id'],)))


@app.get('/api/reports/current')
def report_current(well_id: str, date: str, user=User):
    with transaction() as c:
        _team_ctx(c, user, well_id)
        return reports.find_or_new(c, well_id, date, user['team_id'])


@app.post('/api/reports/check')
def report_check(body: ReportBody, user=User):
    """未保存的填报内容也能实时校验。"""
    with transaction() as c:
        _team_ctx(c, user, body.well_id)
        seg = reports.segment(c, body.well_id, body.report_date, user['team_id'])
        rep = {**body.model_dump(), 'team_id': user['team_id'], 'phase': seg['phase'] if seg else None}
        return reports.validate(c, rep)


@app.post('/api/reports')
def report_save(body: ReportBody, user=User):
    with transaction() as c:
        _team_ctx(c, user, body.well_id)
        rid = reports.save_draft(c, {**body.model_dump(), 'team_id': user['team_id']}, user)
        return reports.get_report(c, rid)


def _own_report(c, user, rid) -> dict:
    r = c.execute('SELECT * FROM daily_report WHERE report_id=?', (rid,)).fetchone()
    if not r:
        raise HTTPException(404, '日报不存在')
    if user['role'] == 'team' and r['team_id'] != user['team_id']:
        raise HTTPException(404, '日报不存在')
    return dict(r)


@app.get('/api/reports/{rid:int}')
def report_get(rid: int, user=User):
    with transaction() as c:
        _own_report(c, user, rid)
        return reports.get_report(c, rid)


@app.put('/api/reports/{rid:int}')
def report_update(rid: int, body: ReportBody, user=User):
    auth.require(user, 'team')
    with transaction() as c:
        _own_report(c, user, rid)
        reports.update_draft(c, rid, body.model_dump(), user)
        return reports.get_report(c, rid)


@app.post('/api/reports/{rid:int}/submit')
def report_submit(rid: int, user=User):
    auth.require(user, 'team')
    with transaction() as c:
        _own_report(c, user, rid)
        checks = reports.validate(c, reports._load(c, rid))
        if any(x['level'] == '阻断' for x in checks):
            raise HTTPException(409, '存在阻断项，不能提交')
        return reports.submit(c, rid, user)


@app.post('/api/reports/{rid:int}/correct')
def report_correct(rid: int, user=User):
    auth.require(user, 'team')
    with transaction() as c:
        _own_report(c, user, rid)
        return reports.get_report(c, reports.correct(c, rid, user))


# ---------- 日报监控（管理端）：提交即生效后的抽查与补救 ----------

MONITOR_TABS = {
    # 待处理：有警告未抽查确认的已生效日报 + 旧流程遗留未生效的“已提交”日报
    'todo': "(d.status='已提交' OR (d.status='已审核' AND d.checked_at IS NULL AND EXISTS ("
            "SELECT 1 FROM validation_alert a WHERE a.report_id=d.report_id AND a.level='警告')))",
    'effective': "d.status='已审核'",
    'returned': "d.status='驳回' AND d.review_comment LIKE '管理_退回%'",
}


class ReturnBody(BaseModel):
    comment: str = ''
    reverse: bool = False


@app.get('/api/monitor/reports')
def monitor_queue(tab: str = 'todo', user=User):
    auth.require(user, 'finance')
    if tab not in MONITOR_TABS:
        raise HTTPException(422, '分类不正确')
    with transaction() as c:
        out = rows(c.execute(
            'SELECT d.report_id, d.well_id, d.report_date, d.version, d.status, d.phase, d.depth_start, d.depth_end, d.reporter, '
            'd.submitted_at, d.reviewer, d.reviewed_at, d.review_comment, d.checked_by, d.checked_at, d.supersedes_report_id, '
            f't.name team FROM daily_report d JOIN team t ON t.team_id=d.team_id WHERE {MONITOR_TABS[tab]} '
            'ORDER BY d.submitted_at DESC, d.report_id DESC LIMIT 200'))
        for r in out:
            r['alerts'] = rows(c.execute("SELECT level, message FROM validation_alert WHERE report_id=? AND level!='提示'",
                                         (r['report_id'],)))
        return out


@app.get('/api/monitor/summary')
def monitor_summary(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return {k: c.execute(f'SELECT COUNT(*) n FROM daily_report d WHERE {w}').fetchone()['n'] for k, w in MONITOR_TABS.items()}


@app.post('/api/reports/{rid:int}/confirm')
def report_confirm(rid: int, body: Comment, user=User):
    """旧流程遗留的“已提交”日报，由管理端确认生效。"""
    auth.require(user, 'finance')
    with transaction() as c:
        _own_report(c, user, rid)
        return reports.take_effect(c, rid, user['who'], body.comment or '管理端确认生效')


@app.post('/api/reports/{rid:int}/check')
def report_check(rid: int, body: Comment, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        _own_report(c, user, rid)
        return reports.check(c, rid, user, body.comment)


@app.post('/api/reports/{rid:int}/return')
def report_return(rid: int, body: ReturnBody, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        _own_report(c, user, rid)
        return reports.return_report(c, rid, user, body.comment, body.reverse)


@app.get('/api/reports/template.xlsx')
def report_template(well_id: str, start: str, end: str, user=User):
    with transaction() as c:
        _team_ctx(c, user, well_id)
        data = excel.report_template(c, well_id, user['team_id'], start, end)
    return _xlsx(data, f'成本日报_{well_id}_{start}_{end}.xlsx')


@app.post('/api/reports/import')
async def report_import(file: UploadFile = File(...), user=User):
    auth.require(user, 'team')
    data = await _read_upload(file)
    with transaction() as c:
        results = excel.import_reports(c, data, user)
        for r in results:
            if r.get('report_id'):
                auth.check_well(c, user, reports._load(c, r['report_id'])['well_id'])
        return results


# ---------- 规则 ----------

class RuleEdit(BaseModel):
    unit_price: float | None = None
    combine_mode: str | None = None
    granularity: str | None = None
    valid_from: str | None = None
    conditions: list[dict] | None = None
    factor_rules: list[dict] | None = None


class NewVersion(BaseModel):
    valid_from: str | None = None


@app.get('/api/rules')
def rule_list(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.list_rules(c, user)


@app.get('/api/rules/{rid:int}')
def rule_detail(rid: int, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.detail(c, user, rid)


class PublishBody(BaseModel):
    change_basis: str = ''   # 变化依据：部室提供的文件、通知、会议纪要等（文号 + 名称）


@app.put('/api/rules/{rid:int}')
def rule_update(rid: int, body: RuleEdit, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.update(c, user, rid, body.model_dump(exclude_none=True))


@app.post('/api/rules/{rid:int}/confirm')
def rule_confirm(rid: int, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.confirm(c, user, rid)


@app.post('/api/rules/{rid:int}/reject')
def rule_reject(rid: int, body: Comment, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.reject(c, user, rid, body.comment)


@app.post('/api/rules/{rid:int}/impact')
def rule_impact(rid: int, user=User):
    """发布前影响预览：按发布试算一遍，返回将新增 / 冲销的事项与各井金额变化，然后整体回滚，不落库。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.impact(c, user, rid)


@app.post('/api/rules/{rid:int}/publish')
def rule_publish(rid: int, body: PublishBody, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.publish(c, user, rid, body.change_basis)


@app.post('/api/rules/{rid:int}/rollback')
def rule_rollback(rid: int, body: Comment, user=User):
    """发布后发现有误：本版本废止、上一版本恢复生效（没有上一版本则停用该规则），受影响月份冲销重算。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.rollback(c, user, rid, body.comment)


@app.post('/api/rules/{rid:int}/new-version')
def rule_new_version(rid: int, body: NewVersion, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rules.new_version(c, user, rid, body.valid_from)


# ---------- AI 规则解析（第一期，管理端）：依据文件 → 规则草稿 ----------

@app.get('/api/ai/status')
def ai_status(user=User):
    auth.require(user, 'finance')
    return {**ai.status(), 'doc_types': aiparse.DOC_TYPES, 'max_chars': aiparse.MAX_CHARS}


@app.get('/api/ai/docs')
def ai_docs(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return aiparse.list_docs(c)


@app.post('/api/ai/docs')
async def ai_upload(file: UploadFile = File(...), doc_type: str = Form(''), doc_name: str = Form(''), doc_no: str = Form(''),
                    counterparty: str = Form(''), valid_from: str = Form(''), user=User):
    auth.require(user, 'finance')
    data = await _read_upload(file)
    with transaction() as c:
        return aiparse.upload(c, file.filename or '依据文件', data, {'doc_type': doc_type, 'doc_name': doc_name, 'doc_no': doc_no,
                                                                     'counterparty': counterparty, 'valid_from': valid_from}, user)


@app.post('/api/ai/docs/{doc_id:int}/parse')
def ai_parse(doc_id: int, user=User):
    """AI 解析成规则草稿（待确认）。失败也留任务记录：先提交事务，再返回 502。"""
    auth.require(user, 'finance')
    with transaction() as c:
        out = aiparse.parse(c, doc_id, user)
    if not out['ok']:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content={'detail': out['error'], 'task': out['task']})
    return out


@app.get('/api/ai/tasks')
def ai_tasks(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return aiparse.list_tasks(c)


# ---------- 费用分摊（企划财务部） ----------

class PoolBody(BaseModel):
    year: str | None = None               # 归属年份，空 = 当年
    item_code: str = ''
    amount: float | None = None          # 上年发生总额
    prev_rig_months: float | None = None  # 上年实际钻机台月
    scope_market: str | None = None       # 大港 / 长庆 / 西北，空 = 全部
    note: str | None = None


@app.get('/api/allocation/options')
def alloc_options(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return allocation.options(c)


@app.get('/api/allocation/pools')
def alloc_pools(period: str | None = None, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return allocation.list_pools(c, period)


@app.get('/api/allocation/pools/{pid:int}')
def alloc_pool(pid: int, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return allocation.detail(c, pid)


@app.post('/api/allocation/preview')
def alloc_preview(body: PoolBody, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return allocation.preview(c, body.model_dump())


@app.post('/api/allocation/pools')
def alloc_create(body: PoolBody, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return allocation.create(c, body.model_dump(), user)


@app.post('/api/allocation/pools/{pid:int}/void')
def alloc_void(pid: int, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return allocation.void(c, pid, user)


# ---------- 计算批次 ----------

@app.get('/api/batches')
def batches(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return rows(c.execute('SELECT * FROM calc_batch ORDER BY batch_id DESC LIMIT 50'))


@app.post('/api/recalc')
def recalc(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        wells, periods = engine.affected_scope(c)
        return engine.recalc(c, wells, periods, '手工重算', user['who'])


# ---------- 单井基础信息（基层队填报 → 汇总） ----------

class InfoBody(BaseModel):
    report_id: int | None = None
    well_id: str = ''
    period: str = ''
    market: str = ''
    captain: str = ''
    rig_type: str = ''
    client: str = ''
    block: str = ''
    well_category: str = ''
    well_type: str = ''
    spud_sections: int = 0
    design_depth: float = 0
    milestones: dict[str, str | None] = {}
    staff: dict[str, float | int | None] = {}


@app.exception_handler(wellinfo.InfoError)
def _info_error(_req, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={'detail': str(exc)})


@app.get('/api/wellinfo/options')
def info_options(user=User):
    with transaction() as c:
        rigs = sorted(set(wellinfo.RIGS) | {r['rig_type'] for r in c.execute('SELECT rig_type FROM team WHERE rig_type IS NOT NULL')})
        team = dict(c.execute('SELECT team_no, name, captain, rig_type FROM team WHERE team_id=?', (user['team_id'],)).fetchone())             if user['team_id'] else None
        return {
            'markets': wellinfo.MARKETS, 'categories': wellinfo.CATEGORIES, 'well_types': wellinfo.WELL_TYPES, 'rigs': rigs,
            'sections': wellinfo.SECTIONS, 'staff': [{'key': k, 'label': v} for k, v in wellinfo.STAFF],
            'clients': [r[0] for r in c.execute('SELECT DISTINCT client FROM well WHERE client IS NOT NULL ORDER BY client')],
            'blocks': [r[0] for r in c.execute('SELECT DISTINCT block FROM well ORDER BY block')],
            'team': team, 'completion_lag_limit': wellinfo.COMPLETION_LAG_LIMIT,
        }


@app.get('/api/wellinfo/mine')
def info_mine(user=User):
    auth.require(user, 'team')
    with transaction() as c:
        return wellinfo.my_wells(c, user)


@app.get('/api/wellinfo/current')
def info_current(well_id: str | None = None, user=User):
    """取某井当前记录（草稿优先）；不带井号返回登记新井的空白表。"""
    auth.require(user, 'team')
    with transaction() as c:
        rec = wellinfo.current(c, well_id, user['team_id']) if well_id else None
        if well_id and not rec:
            raise HTTPException(404, '本队没有该井的基础信息')
        return wellinfo.view(c, rec or wellinfo.template(c, user), user['team_id'])


@app.post('/api/wellinfo/check')
def info_check(body: InfoBody, user=User):
    auth.require(user, 'team')
    with transaction() as c:
        rec = wellinfo.normalize(body.model_dump())
        if body.report_id:
            old = wellinfo._load(c, body.report_id, user['team_id'])
            rec.update(registered_at=old['registered_at'], completion_reported_at=old['completion_reported_at'])
        return {'checks': wellinfo.validate(c, rec, user['team_id']), 'derived': wellinfo.derive(rec)}


@app.post('/api/wellinfo')
def info_save(body: InfoBody, user=User):
    auth.require(user, 'team')
    with transaction() as c:
        rid = wellinfo.save_draft(c, body.model_dump(), user)
        return wellinfo.view(c, wellinfo._load(c, rid), user['team_id'])


@app.post('/api/wellinfo/{rid:int}/submit')
def info_submit(rid: int, user=User):
    auth.require(user, 'team')
    with transaction() as c:
        return wellinfo.submit(c, rid, user)


@app.post('/api/wellinfo/{rid:int}/revise')
def info_revise(rid: int, user=User):
    auth.require(user, 'team')
    with transaction() as c:
        nid = wellinfo.revise(c, rid, user)
        return wellinfo.view(c, wellinfo._load(c, nid), user['team_id'])


@app.get('/api/wellinfo/library')
def info_library(user=User):
    """基础信息库：管理端看全部井，基层队只看本队井。"""
    with transaction() as c:
        return wellinfo.library(c, user)


@app.get('/api/wellinfo/summary')
def info_summary(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return wellinfo.summary(c)


@app.get('/api/wellinfo/summary.xlsx')
def info_summary_xlsx(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        data = excel.well_info_sheet(c)
    return _xlsx(data, '单井基础信息表.xlsx')


@app.get('/api/wellinfo/wells/{well_id}')
def info_well(well_id: str, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return wellinfo.well_detail(c, well_id)


@app.post('/api/wellinfo/wells/{well_id}/return')
def info_return(well_id: str, body: Comment, user=User):
    """退回队伍修改：队伍重新提交前，汇总仍按当前已提交版本。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return wellinfo.return_to_team(c, well_id, body.comment or '', user)


@app.get('/api/wellinfo/returns')
def info_returns(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return wellinfo.pending_returns(c)


@app.post('/api/wellinfo/import')
async def info_import(commit: bool = False, file: UploadFile = File(...), user=User):
    """按《单井基础信息表》格式导入。commit=false 只预览；commit=true 写入。"""
    auth.require(user, 'finance')
    data = await _read_upload(file)
    rows = excel.parse_well_info(data)
    with transaction() as c:
        return wellinfo.import_rows(c, rows, user, commit)


# ---------- 主数据 ----------

@app.get('/api/master/{kind}')
def master_list(kind: str, user=User):
    q = {
        'items': 'SELECT item_code, item_name, parent_code, level, is_leaf, direction, cost_behavior, calc_type, owner_dept, '
                 'report_col, finance_account FROM cost_item ORDER BY sort_no',
        'teams': 'SELECT team_id, team_no, name, rig_type, unit, captain FROM team ORDER BY team_no',
        'materials': 'SELECT m.*, i.item_name FROM material m LEFT JOIN cost_item i ON i.item_code=m.item_code',
        'units': 'SELECT * FROM unit_of_measure',
        'report_lines': 'SELECT l.*, i.item_name, i.owner_dept FROM report_line l LEFT JOIN cost_item i ON i.item_code=l.item_code '
                        'ORDER BY l.section_no, l.sort_no',
    }.get(kind)
    if not q:
        raise HTTPException(404, '未知主数据类别')
    with transaction() as c:
        return rows(c.execute(q))


class TeamBody(BaseModel):
    name: str | None = None
    rig_type: str | None = None
    unit: str | None = None
    captain: str | None = None


class MaterialBody(BaseModel):
    material_code: str | None = None   # 只在新增时使用
    name: str | None = None
    unit: str | None = None
    item_code: str | None = None
    enabled: bool | None = None


class UnitBody(BaseModel):
    unit: str | None = None            # 只在新增时使用
    usage: str | None = None


class LineBody(BaseModel):
    label: str | None = None
    unit: str | None = None
    enabled: bool | None = None
    needs_doc: bool | None = None
    sort_no: int | None = None


def _master_write(fn, *args, user):
    """主数据写操作：仅管理端；只提交请求里实际给出的字段。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return fn(c, *args, user['who'])


@app.put('/api/master/teams/{team_id}')
def master_team(team_id: str, body: TeamBody, user=User):
    return _master_write(master.update_team, team_id, body.model_dump(exclude_unset=True), user=user)


@app.post('/api/master/materials')
def master_material_new(body: MaterialBody, user=User):
    return _master_write(master.create_material, body.model_dump(exclude_unset=True), user=user)


@app.put('/api/master/materials/{code}')
def master_material(code: str, body: MaterialBody, user=User):
    return _master_write(master.update_material, code, body.model_dump(exclude_unset=True, exclude={'material_code'}), user=user)


@app.post('/api/master/units')
def master_unit_new(body: UnitBody, user=User):
    return _master_write(master.create_unit, body.model_dump(exclude_unset=True), user=user)


@app.put('/api/master/units/{unit}')
def master_unit(unit: str, body: UnitBody, user=User):
    return _master_write(master.update_unit, unit, body.model_dump(exclude_unset=True, exclude={'unit'}), user=user)


@app.put('/api/master/report_lines/{key}')
def master_line(key: str, body: LineBody, user=User):
    return _master_write(master.update_line, key, body.model_dump(exclude_unset=True), user=user)


@app.delete('/api/master/report_lines/{key}')
def master_line_delete(key: str, user=User):
    return _master_write(master.delete_line, key, user=user)


@app.put('/api/master/items/{code}')
def master_item(code: str, body: dict, user=User):
    """费用科目只开放说明字段；请求里带编码、层级、方向、成本性态等字段时整单拒绝。"""
    return _master_write(master.update_item, code, body, user=user)


# ---------- 角色与权限 ----------

class NewUser(BaseModel):
    username: str
    name: str | None = None
    role: str
    scope: str | None = None   # 基层队：队号
    password: str


class ResetBody(BaseModel):
    password: str


class EnableBody(BaseModel):
    enabled: bool


@app.get('/api/admin/users')
def admin_users(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        return {'users': [{**u, 'role_name': auth.ROLE_NAMES[u['role']]} for u in accounts.list_users(c)],
                'teams': rows(c.execute('SELECT team_no, name FROM team ORDER BY team_no'))}


@app.post('/api/admin/users')
def admin_create_user(body: NewUser, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        u = accounts.create_user(c, body.username, body.role, body.scope, body.password, body.name)
        log(c, '账号', u['user_id'], '新建', user['who'], f"{auth.ROLE_NAMES[u['role']]} · {u['scope_label']}")
        return u


@app.post('/api/admin/users/{user_id}/reset')
def admin_reset(user_id: str, body: ResetBody, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        accounts.reset_password(c, user_id, body.password)
        log(c, '账号', user_id, '重置密码', user['who'])
    return {'ok': True}


@app.post('/api/admin/users/{user_id}/enable')
def admin_enable(user_id: str, body: EnableBody, user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        accounts.set_enabled(c, user_id, body.enabled, user['user_id'])
        log(c, '账号', user_id, '启用' if body.enabled else '停用', user['who'])
    return {'ok': True}


@app.get('/api/admin/permissions')
def permissions(user=User):
    auth.require(user, 'finance')
    with transaction() as c:
        users = rows(c.execute('SELECT user_id, name, role, who, scope_label FROM app_user WHERE username IS NOT NULL AND enabled=1'))
    return {'roles': [{'id': k, 'name': v} for k, v in auth.ROLE_NAMES.items()],
            'matrix': [{'name': n, 'cells': {'team': p[0], 'finance': p[1]}} for n, p in auth.PERMS],
            'users': users}


# ---------- 前端页面 ----------

DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / 'index.html', headers={'Cache-Control': 'no-cache'})   # 更新后浏览器总能拿到新版本的入口
