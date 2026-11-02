"""经营总览全年同比：未满 12 个月时对比去年同期，满 12 个月时仍对比上年全年。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


FIN = {'X-User-Id': 'u_fin'}


def _rev(client, period: str) -> float:
    return client.get(f'/api/overview?period={period}', headers=FIN).json()['sum']['revenue']


def _insert(conn, period: str, amount: float) -> None:
    conn.execute(
        "INSERT INTO economic_event (event_date, period, well_id, item_code, direction, origin_type, amount, status) "
        "VALUES (?, ?, 'W001', 'R02', '收入', '历史导入', ?, '有效')",
        (period + '-01', period, amount))


def test_partial_year_yoy_uses_same_months_not_full_prior_year(client):
    """上年 12 个月都有数、本年只有 1–N 月时，同比合计应等于上年 1–N 月，而不是上年全年。

    示例库已有 2026-08 与 2026-10，补上 1–7 月和 9 月后，本年月份正好是 1–10 月。
    """
    from app.db import connect
    n = 10
    conn = connect()
    for m in range(1, 13):
        _insert(conn, f'2025-{m:02d}', 1000 * m)          # 1 月 1000 … 12 月 12000，各月可区分
    for m in range(1, n + 1):
        if m in (8, 10):                                  # 示例库已有这两个月
            continue
        _insert(conn, f'2026-{m:02d}', 1)                 # 只用来占住月份，不写 11、12 月
    conn.commit()
    conn.close()

    prior = client.get('/api/overview?period=2025', headers=FIN).json()
    assert prior['months'] == [f'2025-{m:02d}' for m in range(1, 13)]
    full_prior = prior['sum']['revenue']
    same = sum(_rev(client, f'2025-{m:02d}') for m in range(1, n + 1))
    assert same < full_prior                          # 1–N 月严格小于全年，否则断言分不出两种口径

    year = client.get('/api/overview?period=2026', headers=FIN).json()
    assert year['months'] == [f'2026-{m:02d}' for m in range(1, n + 1)]
    assert year['prev']['period'] == '2025'
    assert year['prev']['months'] == [f'2025-{m:02d}' for m in range(1, n + 1)]
    assert [m[-2:] for m in year['prev']['months']] == [m[-2:] for m in year['months']]
    assert year['prev']['sum']['revenue'] == same
    assert year['prev']['sum']['revenue'] != full_prior
    assert year['prev']['sum']['net_profit'] == sum(
        client.get(f'/api/overview?period=2025-{m:02d}', headers=FIN).json()['sum']['net_profit'] for m in range(1, n + 1))

    # 单月仍是环比（上一有数据的月份），不是去年同月
    sep = client.get('/api/overview?period=2026-09', headers=FIN).json()
    assert sep['prev']['period'] == '2026-08' and 'months' not in sep['prev']
    oct_ = client.get('/api/overview?period=2026-10', headers=FIN).json()
    assert oct_['prev']['period'] == '2026-09'


def test_full_year_yoy_still_uses_entire_prior_year(client):
    from app.db import connect
    conn = connect()
    for m in range(1, 13):
        _insert(conn, f'2025-{m:02d}', 1000 * m)
        if m not in (8, 10):                              # 示例库已有这两个月
            _insert(conn, f'2026-{m:02d}', 1)
    conn.commit()
    conn.close()

    prior = client.get('/api/overview?period=2025', headers=FIN).json()
    year = client.get('/api/overview?period=2026', headers=FIN).json()
    assert year['months'] == [f'2026-{m:02d}' for m in range(1, 13)]
    assert year['prev']['period'] == '2025'
    assert year['prev']['months'] == prior['months']
    assert year['prev']['sum']['revenue'] == prior['sum']['revenue']
    assert year['prev']['sum']['net_profit'] == prior['sum']['net_profit']
