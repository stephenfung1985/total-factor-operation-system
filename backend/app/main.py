"""全要素经营管理系统 v3.0 · API

启动：uvicorn app.main:app --reload（在 backend 目录下）
前端构建产物 frontend/dist 存在时，同一端口直接提供页面。
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import accounts, analysis, auth, engine, excel, reports, results_import, rules, wellinfo
from .db import init_db, log, rows, transaction

app = FastAPI(title='全要素经营管理系统 v3.0', version='3.0.0')
User = Depends(auth.current_user)
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


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


def _me(user: dict) -> dict:
    return {**accounts.public(user), 'role_name': auth.ROLE_NAMES[user['role']], 'nav': auth.NAV[user['role']]}


@app.post('/api/auth/login')
def login(body: LoginBody):
    with transaction() as c:
        token, user = accounts.login(c, body.username, body.password)
        log(c, '账号', user['user_id'], '登录', user['who'])
    return {'token': token, 'user': _me(user)}


@app.post('/api/auth/logout')
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith('Bearer '):
        with transaction() as c:
            accounts.logout(c, authorization[7:].strip())
    return {'ok': True}


@app.post('/api/auth/password')
def change_password(body: PasswordBody, user=User):
    with transaction() as c:
        accounts.change_password(c, user['user_id'], body.old_password, body.new_password)
        log(c, '账号', user['user_id'], '修改密码', user['who'])
    return {'ok': True}


@app.get('/api/me')
def me(user=User):
    return _me(user)


# ---------- 经营总览与下钻 ----------

@app.get('/api/periods')
def periods(user=User):
    auth.business(user)
    with transaction() as c:
        return analysis.periods(c)


@app.get('/api/overview')
def overview(period: str, user=User):
    with transaction() as c:
        return analysis.overview(c, user, period)


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
def benchmark(period: str = 'all', dims: str = 'market', market: list[str] = Query([]), block: list[str] = Query([]),
              team: list[str] = Query([]), user=User):
    """经营对标：dims 为分组方式（market / block / team 任意组合，逗号分隔）；
    market / block / team 可重复传参，选定参与对比的具体对象；period 为 YYYY-MM 或 all。"""
    auth.require(user, 'finance')
    with transaction() as c:
        return analysis.benchmark(c, period, [d.strip() for d in dims.split(',') if d.strip()],
                                  {'market': market, 'block': block, 'team': team})


@app.exception_handler(results_import.ImportError_)
def _import_error(_req, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={'detail': str(exc)})


@app.get('/api/results/template.xlsx')
def results_template(user=User):
    auth.require(user, 'finance')
    return _xlsx(results_import.blank_template(), '单井经营情况导入模板.xlsx')


@app.post('/api/results/import')
async def results_import_(commit: bool = False, file: UploadFile = File(...), user=User):
    """单井经营结果按模板导入：commit=false 只预览；导入的井在经营总览备注“历史数据导入”。"""
    auth.require(user, 'finance')
    data = await file.read()
    with transaction() as c:
        return results_import.run(c, data, file.filename or '导入文件.xlsx', user, commit)


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
    auth.business(user)
    with transaction() as c:
        return reports.catalog(c)


@app.get('/api/reports/wells')
def report_wells(user=User):
    auth.require(user, 'team')
    with transaction() as c:
        return rows(c.execute(
            'SELECT DISTINCT w.well_id, w.block, w.well_type, w.status FROM well w JOIN team_well_period p ON p.well_id=w.well_id '
            "WHERE p.team_id=? AND w.status IN ('计划','正钻','完钻') ORDER BY w.well_id", (user['team_id'],)))


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
    auth.business(user)
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


@app.get('/api/review/reports')
def review_queue(status: str = '已提交', user=User):
    auth.require(user, 'dept', 'finance')
    with transaction() as c:
        out = rows(c.execute(
            'SELECT d.report_id, d.well_id, d.report_date, d.version, d.status, d.phase, d.depth_start, d.depth_end, d.reporter, '
            'd.submitted_at, d.reviewer, d.reviewed_at, d.review_comment, t.name team FROM daily_report d JOIN team t ON t.team_id=d.team_id '
            'WHERE d.status=? ORDER BY d.submitted_at DESC, d.report_id DESC LIMIT 100', (status,)))
        for r in out:
            r['alerts'] = rows(c.execute('SELECT level, message FROM validation_alert WHERE report_id=?', (r['report_id'],)))
        return out


@app.post('/api/reports/{rid:int}/approve')
def report_approve(rid: int, body: Comment, user=User):
    auth.require(user, 'dept')
    with transaction() as c:
        return reports.approve(c, rid, user, body.comment)


@app.post('/api/reports/{rid:int}/reject')
def report_reject(rid: int, body: Comment, user=User):
    auth.require(user, 'dept')
    if not (body.comment or '').strip():
        raise HTTPException(422, '驳回必须填写意见')
    with transaction() as c:
        return reports.reject(c, rid, user, body.comment)


@app.get('/api/reports/template.xlsx')
def report_template(well_id: str, start: str, end: str, user=User):
    with transaction() as c:
        _team_ctx(c, user, well_id)
        data = excel.report_template(c, well_id, user['team_id'], start, end)
    return _xlsx(data, f'成本日报_{well_id}_{start}_{end}.xlsx')


@app.post('/api/reports/import')
async def report_import(file: UploadFile = File(...), user=User):
    auth.require(user, 'team')
    data = await file.read()
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
    auth.require(user, 'dept', 'finance')
    with transaction() as c:
        return rules.list_rules(c, user)


@app.get('/api/rules/{rid:int}')
def rule_detail(rid: int, user=User):
    auth.require(user, 'dept', 'finance')
    with transaction() as c:
        return rules.detail(c, user, rid)


@app.put('/api/rules/{rid:int}')
def rule_update(rid: int, body: RuleEdit, user=User):
    with transaction() as c:
        return rules.update(c, user, rid, body.model_dump(exclude_none=True))


@app.post('/api/rules/{rid:int}/confirm')
def rule_confirm(rid: int, user=User):
    with transaction() as c:
        return rules.confirm(c, user, rid)


@app.post('/api/rules/{rid:int}/reject')
def rule_reject(rid: int, body: Comment, user=User):
    with transaction() as c:
        return rules.reject(c, user, rid, body.comment)


@app.post('/api/rules/{rid:int}/publish')
def rule_publish(rid: int, user=User):
    with transaction() as c:
        return rules.publish(c, user, rid)


@app.post('/api/rules/{rid:int}/new-version')
def rule_new_version(rid: int, body: NewVersion, user=User):
    with transaction() as c:
        return rules.new_version(c, user, rid, body.valid_from)


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
    auth.business(user)
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
    data = await file.read()
    rows = excel.parse_well_info(data)
    with transaction() as c:
        return wellinfo.import_rows(c, rows, user, commit)


# ---------- 主数据 ----------

@app.get('/api/master/{kind}')
def master(kind: str, user=User):
    auth.business(user)
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


# ---------- 角色与权限 ----------

class NewUser(BaseModel):
    username: str
    name: str | None = None
    role: str
    scope: str | None = None   # 基层队：队号；专业部室：归口部室
    password: str


class ResetBody(BaseModel):
    password: str


class EnableBody(BaseModel):
    enabled: bool


@app.get('/api/admin/users')
def admin_users(user=User):
    auth.require(user, 'admin')
    with transaction() as c:
        return {'users': [{**u, 'role_name': auth.ROLE_NAMES[u['role']]} for u in accounts.list_users(c)],
                'depts': accounts.DEPTS, 'teams': rows(c.execute('SELECT team_no, name FROM team ORDER BY team_no'))}


@app.post('/api/admin/users')
def admin_create_user(body: NewUser, user=User):
    auth.require(user, 'admin')
    with transaction() as c:
        u = accounts.create_user(c, body.username, body.role, body.scope, body.password, body.name)
        log(c, '账号', u['user_id'], '新建', user['who'], f"{auth.ROLE_NAMES[u['role']]} · {u['scope_label']}")
        return u


@app.post('/api/admin/users/{user_id}/reset')
def admin_reset(user_id: str, body: ResetBody, user=User):
    auth.require(user, 'admin')
    with transaction() as c:
        accounts.reset_password(c, user_id, body.password)
        log(c, '账号', user_id, '重置密码', user['who'])
    return {'ok': True}


@app.post('/api/admin/users/{user_id}/enable')
def admin_enable(user_id: str, body: EnableBody, user=User):
    auth.require(user, 'admin')
    with transaction() as c:
        accounts.set_enabled(c, user_id, body.enabled, user['user_id'])
        log(c, '账号', user_id, '启用' if body.enabled else '停用', user['who'])
    return {'ok': True}


@app.get('/api/admin/permissions')
def permissions(user=User):
    auth.require(user, 'admin')
    with transaction() as c:
        users = rows(c.execute('SELECT user_id, name, role, who, scope_label FROM app_user WHERE username IS NOT NULL AND enabled=1'))
    return {'roles': [{'id': k, 'name': v} for k, v in auth.ROLE_NAMES.items()],
            'matrix': [{'name': n, 'cells': {'team': p[0], 'dept': p[1], 'finance': p[2], 'admin': p[3]}} for n, p in auth.PERMS],
            'users': users}


# ---------- 前端页面 ----------

DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / 'index.html')
