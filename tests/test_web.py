import pytest
from fastapi.testclient import TestClient

from po_dashboard.config import ConfigError, Settings
from po_dashboard.web import create_app


def test_home_shows_title_and_health_strip() -> None:
    r = TestClient(create_app(Settings())).get("/")
    assert r.status_code == 200
    assert "PO Dashboard" in r.text and "Last scan" in r.text


def test_health_endpoint() -> None:
    body = TestClient(create_app(Settings())).get("/health").json()
    assert body["app"] == "ok"


def test_non_loopback_host_refused() -> None:
    with pytest.raises(ConfigError):
        Settings(host="0.0.0.0")  # noqa: S104


def test_remote_model_server_refused() -> None:
    with pytest.raises(ConfigError):
        Settings(ollama_url="http://example.com:11434")
