"""The only module that calls the model server (DEC-01): a reachability check, and the extraction call. The model
server is on this machine; Settings refuses any other address. No document text goes in an error message."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from po_dashboard.config import Settings
from po_dashboard.extraction import prompts, schema

# The first call after Ollama loads a 14b model can take minutes (CLAUDE.md).
EXTRACT_TIMEOUT = 600.0
# A dead server fails in seconds; only the wait for an answer is long.
CONNECT_TIMEOUT = 5.0
CONTEXT_TOKENS = 8192
MAX_REPLY_TOKENS = 2048  # a runaway reply stops here instead of running for minutes


class ModelError(RuntimeError):
    """The model server did not give a usable answer. The message holds no document text."""


class BadAnswer(ModelError):
    """The server answered, but the reply cannot be used (not the schema, or cut off at the reply limit). At temperature
    0 the same text gives the same reply, so trying again would fail the same way: the extractor does not (DEC-54)."""


class ModelUnavailable(ModelError):
    """The model server could not be reached at all (not running, or still starting). The document is not at fault:
    the scanner leaves it waiting and tries again next time, instead of marking it failed."""


@dataclass(frozen=True)
class Extraction:
    answer: dict[str, Any]
    seconds: float
    raw: str
    prompt_tokens: int | None = None  # Ollama's own count of the prompt, to tell when the window was reached


def extract(
    settings: Settings,
    document_text: str,
    *,
    model: str | None = None,
    seed: int = 7,
    timeout: float = EXTRACT_TIMEOUT,
    keep_alive: str = "10m",
) -> Extraction:
    """Read one document's text with the extraction prompt and schema v1. Temperature 0 and a fixed seed, so the same
    text gives the same answer. The text is fenced as data (DEC-09)."""
    body = {
        "model": model or settings.model,
        "stream": False,
        "format": schema.EXTRACTION_SCHEMA,
        "keep_alive": keep_alive,
        "options": {"temperature": 0, "seed": seed, "num_ctx": CONTEXT_TOKENS, "num_predict": MAX_REPLY_TOKENS},
        "messages": [
            {"role": "system", "content": prompts.SYSTEM},
            {"role": "user", "content": prompts.fence(document_text)},
        ],
    }
    started = time.monotonic()
    try:
        wait = httpx.Timeout(timeout, connect=CONNECT_TIMEOUT)
        response = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=wait)
        response.raise_for_status()
        reply = response.json()
        raw = reply["message"]["content"]
        cut_off = reply.get("done_reason") == "length"
        counted = reply.get("prompt_eval_count")
        prompt_tokens = counted if isinstance(counted, int) and not isinstance(counted, bool) else None
    except (httpx.ConnectError, httpx.ConnectTimeout) as problem:
        raise ModelUnavailable("the model server is not reachable") from problem
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as problem:
        raise ModelError(f"the model server gave no answer ({type(problem).__name__})") from problem
    seconds = time.monotonic() - started
    if cut_off:
        raise BadAnswer("the reply was cut off at the reply limit")
    try:
        answer = schema.parse_answer(raw)
    except schema.NotAnAnswer as problem:
        raise BadAnswer(str(problem)) from problem
    return Extraction(answer=answer, seconds=seconds, raw=raw, prompt_tokens=prompt_tokens)


def unload(settings: Settings, model: str) -> None:
    """Ask the server to free the model's memory. Best effort."""
    try:
        free = {"model": model, "messages": [], "keep_alive": 0}
        httpx.post(f"{settings.ollama_url}/api/chat", json=free, timeout=httpx.Timeout(60, connect=CONNECT_TIMEOUT))
    except httpx.HTTPError:
        pass


def is_reachable(settings: Settings, timeout: float = 1.0) -> bool:
    """True if Ollama answers on this machine and has the configured model. Sends no document text."""
    try:
        response = httpx.get(f"{settings.ollama_url}/api/tags", timeout=timeout)
        response.raise_for_status()
        models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return False
    return any(isinstance(m, dict) and m.get("name") == settings.model for m in models)
