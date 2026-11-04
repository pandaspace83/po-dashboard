"""The only module that calls the model server (DEC-01). Phase 0: a reachability check only."""

from __future__ import annotations

import httpx

from po_dashboard.config import Settings


def is_reachable(settings: Settings, timeout: float = 1.0) -> bool:
    """True if Ollama answers on this machine and has the configured model. Sends no document text."""
    try:
        response = httpx.get(f"{settings.ollama_url}/api/tags", timeout=timeout)
        response.raise_for_status()
        models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return False
    return any(isinstance(m, dict) and m.get("name") == settings.model for m in models)
