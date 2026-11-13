import importlib

import httpx
import pytest

from po_dashboard.config import Settings
from po_dashboard.models import llm as llm_module


def check(monkeypatch: pytest.MonkeyPatch, response: httpx.Response | Exception) -> bool:
    # The autouse fixture replaces is_reachable, so test a fresh copy of the real function.
    monkeypatch.undo()
    real = importlib.reload(llm_module)

    def fake_get(url: str, timeout: float) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        response.request = httpx.Request("GET", url)
        return response

    monkeypatch.setattr(httpx, "get", fake_get)
    return real.is_reachable(Settings())


def test_true_when_the_configured_model_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"models": [{"name": Settings().model}]}
    assert check(monkeypatch, httpx.Response(200, json=body))


def test_false_when_ollama_runs_but_the_model_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not check(monkeypatch, httpx.Response(200, json={"models": [{"name": "other:1b"}]}))


def test_false_when_ollama_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not check(monkeypatch, httpx.ConnectError("refused"))


def test_false_on_an_error_status_or_a_bad_body(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not check(monkeypatch, httpx.Response(500))
    assert not check(monkeypatch, httpx.Response(200, text="not json"))
    assert not check(monkeypatch, httpx.Response(200, json=["a list"]))


def test_only_the_gateway_talks_to_the_model_server() -> None:
    """NFR-10, DEC-01: no other module names the model server's address or its routes."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "po_dashboard"
    allowed = {src / "models" / "llm.py", src / "config.py"}  # config holds the address and refuses a non-local one
    markers = ("ollama_url", "/api/chat", "/api/tags", "/api/generate", "11434")
    offenders = [
        str(p.relative_to(src))
        for p in sorted(src.rglob("*.py"))
        if p not in allowed and any(m in p.read_text(encoding="utf-8") for m in markers)
    ]
    assert offenders == [], f"model server reached outside models/llm.py: {offenders}"
    assert "ollama_url" in (src / "models" / "llm.py").read_text(encoding="utf-8")
