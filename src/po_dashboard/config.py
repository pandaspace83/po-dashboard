"""Settings read from the environment, with safe defaults.

Two rules are enforced here, not left to the caller:
- the app listens on this machine's own address unless network access is turned on later (NFR-09, OPEN-10);
- the model server must be on this machine (DEC-01).
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LOOPBACK_NAMES = {"localhost"}
DEFAULT_MODEL = "qwen2.5:14b-instruct"  # DEC-05
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_PORT = 8000


class ConfigError(ValueError):
    """A setting breaks a rule the app keeps (local only, loopback only)."""


def is_loopback_host(host: str) -> bool:
    """True for 127.0.0.0/8, ::1 and localhost; false for anything reachable from the network."""
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def default_data_dir() -> Path:
    """Per-user data folder on the Mac's own storage, outside the repo and outside iCloud (ARCHITECTURE §11)."""
    return Path.home() / "Library" / "Application Support" / "PO Dashboard"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    data_dir: Path = default_data_dir()
    model: str = DEFAULT_MODEL
    ollama_url: str = DEFAULT_OLLAMA_URL

    def __post_init__(self) -> None:
        if not is_loopback_host(self.host):
            raise ConfigError(
                f"host {self.host!r} is not this machine's own address. Network access needs sign-in "
                "and HTTPS first (DEC-20, OPEN-10) and is not built yet."
            )
        ollama_host = urlparse(self.ollama_url).hostname or ""
        if not is_loopback_host(ollama_host):
            raise ConfigError(
                f"the model server {self.ollama_url!r} is not on this machine; models stay local (DEC-01)."
            )
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"port {self.port} is not valid")

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            host=env.get("PO_DASHBOARD_HOST", "127.0.0.1"),
            port=int(env.get("PO_DASHBOARD_PORT", DEFAULT_PORT)),
            data_dir=Path(env["PO_DASHBOARD_DATA"]) if "PO_DASHBOARD_DATA" in env else default_data_dir(),
            model=env.get("PO_DASHBOARD_MODEL", DEFAULT_MODEL),
            ollama_url=env.get("PO_DASHBOARD_OLLAMA_URL", DEFAULT_OLLAMA_URL),
        )
