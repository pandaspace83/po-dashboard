"""The only module that calls the model server (DEC-01). Phase 0: a reachability check only."""

from __future__ import annotations

import httpx

from po_dashboard.config import Settings


def is_reachable(settings: Settings, timeout: float = 1.0) -> bool:
    """True if Ollama answers on this machine. Sends no document text."""
    try:
        return httpx.get(f"{settings.ollama_url}/api/tags", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False
