import pytest

from po_dashboard.models import llm


@pytest.fixture(autouse=True)
def no_real_model_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fast tests never depend on Ollama running. tests/test_llm.py tests the check itself."""
    monkeypatch.setattr(llm, "is_reachable", lambda settings, timeout=1.0: False)


@pytest.fixture(autouse=True)
def no_real_keychain() -> None:
    """No test ever reads or writes the owner's real Keychain (a Gmail token lives there). A test that needs secrets
    passes its own in-memory store."""
    import keyring
    from keyring.backends.fail import Keyring as Refuse

    keyring.set_keyring(Refuse())
