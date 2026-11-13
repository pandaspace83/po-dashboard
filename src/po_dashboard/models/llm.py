"""The only module that calls the model server (DEC-01): a reachability check, and the extraction call. The model
server is on this machine; Settings refuses any other address. No document text goes in an error message."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

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


class ModelBusy(ModelError):
    """A question was turned away because MAX_WAITING_QUESTIONS others already wait for the model (DEC-68)."""


class ModelUnavailable(ModelError):
    """The model server could not be reached at all (not running, or still starting). The document is not at fault:
    the scanner leaves it waiting and tries again next time, instead of marking it failed."""


@dataclass(frozen=True)
class Extraction:
    answer: dict[str, Any]
    seconds: float
    raw: str
    prompt_tokens: int | None = None  # Ollama's own count of the prompt, to tell when the window was reached
    reply_tokens: int | None = None  # Ollama's own count of the reply


MAX_WAITING_QUESTIONS = 3  # DEC-68: a fourth question is told the assistant is busy
CHAT_TIMEOUT = 600.0

Kind = Literal["document", "question"]


class _Gate:
    """One model call at a time across the app (DEC-68). A question takes the next turn ahead of every waiting document;
    a call already running is never interrupted. Questions go in the order they came. At most MAX_WAITING_QUESTIONS
    questions wait; a further one is refused at once with `ModelBusy`."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._running = False
        self._questions: deque[object] = deque()
        self._documents = 0

    @contextmanager
    def turn(self, kind: Kind) -> Iterator[None]:
        me = object()
        with self._cond:
            if kind == "question":
                if len(self._questions) >= MAX_WAITING_QUESTIONS:
                    raise ModelBusy("the assistant is busy: other questions are already waiting")
                self._questions.append(me)
                self._cond.wait_for(lambda: not self._running and self._questions[0] is me)
                self._questions.popleft()
            else:
                self._documents += 1
                self._cond.wait_for(lambda: not self._running and not self._questions)
                self._documents -= 1
            self._running = True
        try:
            yield
        finally:
            with self._cond:
                self._running = False
                self._cond.notify_all()

    def waiting(self) -> tuple[int, int]:
        """(questions, documents) waiting now. For the health strip and tests."""
        with self._cond:
            return len(self._questions), self._documents


gate = _Gate()


@dataclass(frozen=True)
class _Reply:
    raw: str
    seconds: float
    cut_off: bool
    prompt_tokens: int | None
    reply_tokens: int | None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _chat(settings: Settings, body: dict[str, Any], timeout: float, kind: Kind) -> _Reply:
    """The one HTTP call to the model server, taken in turn. The seconds are the call's, not the wait for the turn.
    Every error message is free of prompt and reply text."""
    with gate.turn(kind):
        started = time.monotonic()
        try:
            wait = httpx.Timeout(timeout, connect=CONNECT_TIMEOUT)
            response = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=wait)
            response.raise_for_status()
            reply = response.json()
            raw = reply["message"]["content"]
            cut_off = reply.get("done_reason") == "length"
            prompt_tokens, reply_tokens = _count(reply.get("prompt_eval_count")), _count(reply.get("eval_count"))
        except (httpx.ConnectError, httpx.ConnectTimeout) as problem:
            raise ModelUnavailable("the model server is not reachable") from problem
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as problem:
            raise ModelError(f"the model server gave no answer ({type(problem).__name__})") from problem
        seconds = time.monotonic() - started
    return _Reply(raw, seconds, cut_off, prompt_tokens, reply_tokens)


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
    text gives the same answer. The text is fenced as data (DEC-09). Waits behind any question (DEC-68)."""
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
    got = _chat(settings, body, timeout, "document")
    if got.cut_off:
        raise BadAnswer("the reply was cut off at the reply limit")
    try:
        answer = schema.parse_answer(got.raw)
    except schema.NotAnAnswer as problem:
        raise BadAnswer(str(problem)) from problem
    return Extraction(answer=answer, seconds=got.seconds, raw=got.raw, prompt_tokens=got.prompt_tokens,
                      reply_tokens=got.reply_tokens)  # fmt: skip


def chat_json(
    settings: Settings,
    *,
    system: str,
    user: str,
    json_schema: dict[str, Any],
    model: str | None = None,
    seed: int = 7,
    max_reply_tokens: int = 512,
    timeout: float = CHAT_TIMEOUT,
    keep_alive: str = "10m",
) -> Extraction:
    """A question's model call, for any schema (DEC-68): the reply is a JSON object, constrained by `json_schema` at
    the server, and returned as a dict for the caller's own contract to check. Takes the next turn ahead of waiting
    documents; raises `ModelBusy` at once when 3 questions already wait. The caller fences any record text it puts in
    `user` (DEC-09). Temperature 0 and a fixed seed."""
    body = {
        "model": model or settings.model,
        "stream": False,
        "format": json_schema,
        "keep_alive": keep_alive,
        "options": {"temperature": 0, "seed": seed, "num_ctx": CONTEXT_TOKENS, "num_predict": max_reply_tokens},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    got = _chat(settings, body, timeout, "question")
    if got.cut_off:
        raise BadAnswer("the reply was cut off at the reply limit")
    try:
        answer = json.loads(got.raw)
    except ValueError as problem:
        raise BadAnswer("the reply is not JSON") from problem
    if not isinstance(answer, dict):
        raise BadAnswer("the reply is not a JSON object")
    return Extraction(answer=answer, seconds=got.seconds, raw=got.raw, prompt_tokens=got.prompt_tokens,
                      reply_tokens=got.reply_tokens)  # fmt: skip


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
