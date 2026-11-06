"""费用规则入库三级流程：普通账户提交 → 费用管理部室确认/退回 → 系统管理员发布。

后端按角色拒绝越权。升级前已在库里的规则视为已发布，不重走这三级。
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


def H(user):
    return {'X-User-Id': user}


FIN, COST, ADMIN, TEAM = H('u_fin'), H('u_cost'), H('u_admin'), H('u_team')


def _draft(client):
    mud = next(r for r in client.get('/api/rules', headers=FIN).json() if r['rule_code'] == 'RL-C2-1-02-01')
    return client.post(f"/api/rules/{mud['rule_id']}/new-version", json={'valid_from': '2026-09-01'}, headers=FIN).json()


def test_three_roles_cannot_skip_steps(client):
    d = _draft(client)
    rid = d['rule']['rule_id']
    assert d['can_submit'] and not d['can_confirm'] and not d['can_publish']
    # 草稿未提交：部室不能确认，管理员不能发布，填报端不能提交
    assert client.post(f'/api/rules/{rid}/confirm', headers=COST).status_code == 409
    assert client.post(f'/api/rules/{rid}/publish', json={'change_basis': '调价通知 2026'}, headers=ADMIN).status_code == 409
    assert client.post(f'/api/rules/{rid}/submit', headers=TEAM).status_code == 403
    assert client.post(f'/api/rules/{rid}/submit', headers=COST).status_code == 403
    assert client.post(f'/api/rules/{rid}/submit', headers=ADMIN).status_code == 403

    submitted = client.post(f'/api/rules/{rid}/submit', headers=FIN).json()
    assert submitted['rule']['status'] == '待确认' and submitted['can_confirm'] is False
    assert client.put(f'/api/rules/{rid}', json={'unit_price': 1}, headers=FIN).status_code == 409   # 提交后锁定
    # 确认只属于费用管理部室；填报端在入口即被拒绝
    assert client.post(f'/api/rules/{rid}/confirm', headers=TEAM).status_code == 403
    for who in (FIN, ADMIN):
        r = client.post(f'/api/rules/{rid}/confirm', headers=who)
        assert r.status_code == 403 and '费用管理部室' in r.json()['detail']
    confirmed = client.post(f'/api/rules/{rid}/confirm', headers=COST).json()
    assert confirmed['rule']['status'] == '待发布' and confirmed['backtest']['result'] in ('通过', '豁免')
    # 发布只属于系统管理员；填报端在入口即被拒绝
    assert client.post(f'/api/rules/{rid}/publish', json={'change_basis': '泥浆服务调价通知 2026-09'}, headers=TEAM).status_code == 403
    for who in (FIN, COST):
        r = client.post(f'/api/rules/{rid}/publish', json={'change_basis': '泥浆服务调价通知 2026-09'}, headers=who)
        assert r.status_code == 403 and '系统管理员' in r.json()['detail']
    published = client.post(f'/api/rules/{rid}/publish', json={'change_basis': '泥浆服务调价通知 2026-09'}, headers=ADMIN).json()
    assert published['rule']['status'] == '已发布'
    assert any(x['action'] == '发布' and '系统管理员' in x['operator'] for x in published['logs'])
    # 回滚同样只属于系统管理员
    assert client.post(f'/api/rules/{rid}/rollback', json={'comment': '试错'}, headers=FIN).status_code == 403
    assert client.post(f'/api/rules/{rid}/rollback', json={'comment': '试错'}, headers=COST).status_code == 403


def test_department_return_then_resubmit(client):
    d = _draft(client)
    rid = d['rule']['rule_id']
    client.post(f'/api/rules/{rid}/submit', headers=FIN)
    assert client.post(f'/api/rules/{rid}/reject', json={'comment': '  '}, headers=COST).status_code == 422
    assert client.post(f'/api/rules/{rid}/reject', json={'comment': '单价与合同不符'}, headers=FIN).status_code == 403
    back = client.post(f'/api/rules/{rid}/reject', json={'comment': '单价与合同不符'}, headers=COST).json()
    assert back['rule']['status'] == '草稿' and not back['can_edit']   # 退回人不是提交人，不能改
    seen = client.get(f'/api/rules/{rid}', headers=FIN).json()
    assert seen['can_edit'] and seen['can_submit']
    assert any(x['action'] == '退回' and x['comment'] == '单价与合同不符' for x in seen['logs'])
    client.put(f'/api/rules/{rid}', json={'unit_price': 430}, headers=FIN)
    again = client.post(f'/api/rules/{rid}/submit', headers=FIN).json()
    assert again['rule']['status'] == '待确认'
    # 部室确认后、管理员发布前，部室仍可退回
    ready = client.post(f'/api/rules/{rid}/confirm', headers=COST).json()
    assert ready['rule']['status'] == '待发布'
    returned = client.post(f'/api/rules/{rid}/reject', json={'comment': '生效日有误'}, headers=COST).json()
    assert returned['rule']['status'] == '草稿'
    assert client.post(f'/api/rules/{rid}/publish', json={'change_basis': '不应发布'}, headers=ADMIN).status_code == 409


def test_existing_rules_migrate_as_published(tmp_path, monkeypatch):
    """旧库没有 submitted_by 列：升级时未发布的规则改为已发布，已废止的不动，且只迁移一次。"""
    db = tmp_path / 'old.db'
    monkeypatch.setenv('TFOS_DB', str(db))
    from app.db import SCHEMA, connect, init_db
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text(encoding='utf-8'))
    conn.execute("INSERT INTO cost_item (item_code, item_name, level, is_leaf, direction, cost_behavior, sort_no) "
                 "VALUES ('C2-1-04', '柴油', 3, 1, '成本', '变动', 1)")
    conn.execute(
        'INSERT INTO economic_rule (rule_code, version, name, item_code, pricing_method, calc_order, unit_price, unit, '
        "valid_from, status, origin) VALUES ('RL-OLD', 1, '旧草稿', 'C2-1-04', '单价', 2, 10, '吨', '2025-01-01', '待确认', '人工录入')")
    conn.execute(
        'INSERT INTO economic_rule (rule_code, version, name, item_code, pricing_method, calc_order, unit_price, unit, '
        "valid_from, status, origin, published_at) VALUES ('RL-KEEP', 1, '已在库', 'C2-1-04', '单价', 2, 10, '吨', "
        "'2025-01-01', '已发布', '历史反算', '2025-01-02 08:00:00')")
    conn.execute(
        'INSERT INTO economic_rule (rule_code, version, name, item_code, pricing_method, calc_order, unit_price, unit, '
        "valid_from, status, origin) VALUES ('RL-DEAD', 1, '已废止', 'C2-1-04', '单价', 2, 10, '吨', '2025-01-01', '已废止', '人工录入')")
    conn.commit()
    conn.close()

    init_db()
    c = connect()
    rows = {r['rule_code']: dict(r) for r in c.execute('SELECT rule_code, status, published_at, confirmed_by FROM economic_rule')}
    assert rows['RL-OLD']['status'] == '已发布' and rows['RL-OLD']['confirmed_by'] == '系统迁移' and rows['RL-OLD']['published_at']
    assert rows['RL-KEEP']['status'] == '已发布' and rows['RL-KEEP']['published_at'] == '2025-01-02 08:00:00'
    assert rows['RL-DEAD']['status'] == '已废止'
    n = c.execute("SELECT COUNT(*) FROM approval_log WHERE action='迁移' AND object_id IN "
                  "(SELECT CAST(rule_id AS TEXT) FROM economic_rule WHERE rule_code='RL-OLD')").fetchone()[0]
    assert n == 1
    c.close()
    init_db()   # 再次启动不再迁移
    c = connect()
    n2 = c.execute("SELECT COUNT(*) FROM approval_log WHERE action='迁移'").fetchone()[0]
    c.close()
    assert n2 == 1


def test_old_role_check_accepts_cost_and_admin(tmp_path, monkeypatch):
    """旧库角色约束只有 finance / team / dept：升级后能建立费用管理部室和系统管理员，原账号还在。"""
    db = tmp_path / 'roles.db'
    monkeypatch.setenv('TFOS_DB', str(db))
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE app_user (user_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                 "role TEXT NOT NULL CHECK (role IN ('finance', 'team', 'dept')), who TEXT NOT NULL, "
                 "team_id TEXT, dept TEXT, scope_label TEXT NOT NULL)")
    conn.execute("INSERT INTO app_user VALUES ('keep', '保留', 'finance', '企划财务部 · keep', NULL, NULL, '全部')")
    conn.commit()
    conn.close()
    from app.db import connect, init_db
    init_db()
    c = connect()
    assert c.execute("SELECT role FROM app_user WHERE user_id='keep'").fetchone()['role'] == 'finance'
    assert c.execute("SELECT role FROM app_user WHERE username='feiyong'").fetchone()['role'] == 'cost'
    assert c.execute("SELECT role FROM app_user WHERE username='admin'").fetchone()['role'] == 'admin'
    sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='app_user'").fetchone()['sql']
    assert 'cost' in sql and 'admin' in sql
    c.close()
