import pytest


@pytest.fixture(autouse=True)
def _demo_auth(monkeypatch):
    """测试用请求头 X-User-Id 直接指定账号；正式运行不开启。"""
    monkeypatch.setenv('TFOS_DEMO_AUTH', '1')
