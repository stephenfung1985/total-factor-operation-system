"""经营总览排行所需字段：甲方单位、钻机型号与分档。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


@pytest.mark.parametrize('rig,cls', [('ZJ30D', '50 型'), ('ZJ40J', '50 型'), ('ZJ50D', '50 型'), ('ZJ70D', '70 型'),
                                     ('ZJ80D', '80 型以上'), ('ZJ90D', '80 型以上'), ('—', None), (None, None)])
def test_rig_class(rig, cls):
    from app.analysis import rig_class
    assert rig_class(rig) == cls


def test_overview_rows_carry_client_and_rig(client):
    o = client.get('/api/overview?period=2026', headers={'X-User-Id': 'u_fin'}).json()
    w = next(x for x in o['wells'] if x['well_id'] == 'W001')
    assert w['client'] and w['rig_type'] and w['rig_class'] in ('50 型', '70 型', '80 型以上')
    nav = client.get('/api/me', headers={'X-User-Id': 'u_fin'}).json()['nav']
    assert 'wellinfo' not in {i['id'] for g in nav for i in g['items']}      # 管理端不再有基础信息库菜单
