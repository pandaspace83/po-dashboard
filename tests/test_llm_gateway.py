"""PD-9.2: the assistant's model call and the one-call-at-a-time lock (DEC-68). The model server is replaced."""

import json
import threading
import time
from typing import Any

import httpx
import pytest

from po_dashboard.config import Settings
from po_dashboard.models import llm

SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"]}


def reply(content: str, **extra: Any) -> httpx.Response:
    body = {"message": {"content": content}, "prompt_eval_count": 40, "eval_count": 7, **extra}
    return httpx.Response(200, json=body, request=httpx.Request("POST", "http://127.0.0.1:11434/api/chat"))


def serve(monkeypatch: pytest.MonkeyPatch, answer: httpx.Response | Exception) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def post(url: str, json: dict[str, Any], timeout: Any) -> httpx.Response:
        sent.append(json)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(httpx, "post", post)
    return sent


def ask(**kw: Any) -> llm.Extraction:
    return llm.chat_json(Settings(), system="sys", user="a question", json_schema=SCHEMA, **kw)


def test_chat_json_sends_any_schema_at_temperature_zero_and_keeps_seconds_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = serve(monkeypatch, reply('{"intent": "ASK"}'))
    got = ask()
    assert got.answer == {"intent": "ASK"}
    assert (got.prompt_tokens, got.reply_tokens) == (40, 7)
    assert got.seconds >= 0
    body = sent[0]
    assert body["format"] == SCHEMA and body["options"]["temperature"] == 0 and body["options"]["num_predict"] == 512
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_extract_keeps_the_reply_token_count_too(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, reply(json.dumps({"doc_type": "other"})))
    got = llm.extract(Settings(), "text")
    assert (got.prompt_tokens, got.reply_tokens) == (40, 7)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"text"'])
def test_a_reply_that_is_not_a_json_object_is_a_bad_answer(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    serve(monkeypatch, reply(content))
    with pytest.raises(llm.BadAnswer):
        ask()


def test_a_reply_cut_off_at_the_limit_is_a_bad_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, reply('{"intent": "A', done_reason="length"))
    with pytest.raises(llm.BadAnswer):
        ask()


def test_errors_are_sorted_and_hold_no_question_text(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "PO-777 is secret"
    serve(monkeypatch, httpx.ConnectError(secret))
    with pytest.raises(llm.ModelUnavailable) as down:
        llm.chat_json(Settings(), system="s", user=secret, json_schema=SCHEMA)
    serve(monkeypatch, httpx.ReadTimeout(secret))
    with pytest.raises(llm.ModelError) as slow:
        llm.chat_json(Settings(), system="s", user=secret, json_schema=SCHEMA)
    assert secret not in str(down.value) and secret not in str(slow.value)
    assert not isinstance(slow.value, (llm.BadAnswer, llm.ModelUnavailable))


def test_the_gate_is_free_again_after_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, httpx.ConnectError("x"))
    with pytest.raises(llm.ModelUnavailable):
        ask()
    assert llm.gate.waiting() == (0, 0)
    serve(monkeypatch, reply('{"intent": "ASK"}'))
    assert ask().answer == {"intent": "ASK"}


def _until(condition: Any, seconds: float = 5.0) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


def test_one_call_at_a_time_questions_first_in_order_and_a_fourth_is_busy() -> None:
    order: list[str] = []
    hold = threading.Event()
    gate = llm._Gate()

    def run(name: str, kind: llm.Kind) -> None:
        with gate.turn(kind):
            order.append(name)
            if name == "running":
                hold.wait(5)

    def start(name: str, kind: llm.Kind, waiting: tuple[int, int]) -> threading.Thread:
        t = threading.Thread(target=run, args=(name, kind))
        t.start()
        _until(lambda: gate.waiting() == waiting)
        return t

    first = threading.Thread(target=run, args=("running", "document"))
    first.start()
    _until(lambda: order == ["running"])
    threads = [
        start("doc", "document", (0, 1)),
        start("q1", "question", (1, 1)),
        start("q2", "question", (2, 1)),
        start("q3", "question", (3, 1)),
    ]
    with pytest.raises(llm.ModelBusy):  # the fourth question waiting
        with gate.turn("question"):
            pytest.fail("a fourth waiting question must be refused")
    assert order == ["running"]  # nothing else ran beside it, and the running call was not interrupted
    hold.set()
    for t in [first, *threads]:
        t.join(5)
    assert order == ["running", "q1", "q2", "q3", "doc"]
    assert gate.waiting() == (0, 0)


def test_two_calls_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    inside, most = 0, 0
    lock = threading.Lock()

    def post(url: str, json: dict[str, Any], timeout: Any) -> httpx.Response:
        nonlocal inside, most
        with lock:
            inside += 1
            most = max(most, inside)
        time.sleep(0.02)
        with lock:
            inside -= 1
        return reply('{"intent": "ASK"}')

    monkeypatch.setattr(httpx, "post", post)
    threads = [threading.Thread(target=ask) for _ in range(3)]
    [t.start() for t in threads]
    [t.join(5) for t in threads]
    assert most == 1
