from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from po_dashboard.config import ConfigError, Settings
from po_dashboard.web import create_app


def test_health_says_only_up(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1")
    assert client.get("/health").json() == {"status": "ok"}


def test_non_loopback_host_refused() -> None:
    with pytest.raises(ConfigError):
        Settings(host="0.0.0.0")  # noqa: S104


def test_remote_model_server_refused() -> None:
    with pytest.raises(ConfigError):
        Settings(ollama_url="http://example.com:11434")


def test_no_api_schema_is_served(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1")
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code in (303, 401, 404)
