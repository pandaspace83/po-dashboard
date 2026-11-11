"""Logging (engineering review, 2026-11-10; DEC-13): useful lines, no secrets, no message text, private files."""

from __future__ import annotations

import logging
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from po_dashboard.ops import logs
from po_dashboard.web import create_app
from test_gate import ORIGIN, PW, admin_client, env, new_client, sign_in, token_in  # noqa: F401


@pytest.fixture
def log_file(env):  # noqa: ANN001, ANN201, F811
    path = logs.configure(env[2].data_dir)
    yield path
    logger = logging.getLogger(logs.LOGGER)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.propagate = True


def lines(path: Path) -> list[str]:
    for handler in logging.getLogger(logs.LOGGER).handlers:
        handler.flush()
    return path.read_text(encoding="utf-8").splitlines()


def test_every_response_has_a_request_id_and_a_time_and_one_log_line(admin_client: TestClient, log_file: Path) -> None:  # noqa: F811
    response = admin_client.get("/orders")
    rid = response.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{12}", rid) and re.fullmatch(r"app;dur=\d+", response.headers["server-timing"])
    mine = [line for line in lines(log_file) if f"id={rid}" in line]
    assert len(mine) == 1 and "GET /orders status=200" in mine[0] and re.search(r"ms=\d+", mine[0])


def test_the_log_has_the_route_and_never_the_query_text_or_the_ids(admin_client: TestClient, log_file: Path) -> None:  # noqa: F811
    admin_client.get("/orders?q=SECRET-VENDOR-QUERY&sort=vendor")
    admin_client.get("/orders/987654")
    text = "\n".join(lines(log_file))
    assert "SECRET-VENDOR-QUERY" not in text and "987654" not in text and "/orders/{n}" in text


def test_no_password_or_token_reaches_the_log(env, log_file: Path, caplog) -> None:  # noqa: ANN001, F811
    caplog.set_level(logging.DEBUG)
    client = new_client(env[0])
    form = client.get("/sign-in")
    client.post("/sign-in", data={"email": "boss@example.com", "password": "SENTINEL-WRONG-PASSWORD-77",
                                  "csrf_token": token_in(form.text)}, headers=ORIGIN)
    assert sign_in(client, "boss@example.com").status_code in (200, 303)
    cookie = client.cookies.get("po_session") or ""
    text = "\n".join(lines(log_file)) + caplog.text
    assert "SENTINEL-WRONG-PASSWORD-77" not in text and PW not in text
    assert cookie == "" or cookie not in text


def test_an_unhandled_error_logs_its_type_and_route_but_not_its_message(env, log_file: Path) -> None:  # noqa: ANN001, F811
    app = env[0]

    def boom() -> None:
        raise RuntimeError("SECRET-BODY-TEXT from a vendor email")

    app.add_api_route("/boom", boom)
    client = new_client(app)
    sign_in(client, "boss@example.com")
    safe = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    safe.cookies.update(client.cookies)
    assert safe.get("/boom").status_code == 500
    text = "\n".join(lines(log_file))
    assert "unhandled" in text and "RuntimeError" in text and "/boom" in text and "SECRET-BODY-TEXT" not in text


def test_the_log_files_are_private(log_file: Path) -> None:
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_file.parent.stat().st_mode) == 0o700


def test_configuring_twice_adds_no_second_handler(env) -> None:  # noqa: ANN001, F811
    logs.configure(env[2].data_dir)
    logs.configure(env[2].data_dir)
    handlers = [h for h in logging.getLogger(logs.LOGGER).handlers if h.__class__.__name__ == "RotatingFileHandler"]
    assert len(handlers) == 1
    for handler in handlers:
        handler.close()
        logging.getLogger(logs.LOGGER).removeHandler(handler)
    logging.getLogger(logs.LOGGER).propagate = True


def test_the_log_is_set_up_when_the_app_serves() -> None:
    assert "logs.configure" in (Path(__file__).parent.parent / "src" / "po_dashboard" / "cli.py").read_text()
    assert create_app  # imported so a broken import fails here too
