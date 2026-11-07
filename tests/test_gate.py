"""The sign-in pages and the default-deny gate, through the real app (PD-1.9c)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.config import Settings
from po_dashboard.db.migrate import upgrade
from po_dashboard.web import create_app

PW = "correct horse battery"
ORIGIN = {"Origin": "http://127.0.0.1"}
# A script tag is fine only when it loads a file from this site: the page policy forbids inline script.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=\"/static/)", re.I)


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201
    settings = Settings(data_dir=tmp_path / "data")
    engine = upgrade(settings)
    with Session(engine) as db:
        service.create_user(db, "boss@example.com", "admin", password=PW)
        service.create_user(db, "buyer@example.com", "buyer", password=PW)
    app = create_app(settings, engine)
    yield app, engine, settings
    engine.dispose()


def new_client(app) -> TestClient:  # noqa: ANN001
    return TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)


def token_in(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)  # type: ignore[union-attr]


def sign_in(client: TestClient, email: str, password: str = PW):  # noqa: ANN201
    form = client.get("/sign-in")
    return client.post(
        "/sign-in",
        data={"email": email, "password": password, "csrf_token": token_in(form.text)},
        headers=ORIGIN,
    )


def post(client: TestClient, path: str, data: dict[str, str] | None = None):  # noqa: ANN201
    page = client.get("/")
    return client.post(path, data={"csrf_token": token_in(page.text), **(data or {})}, headers=ORIGIN)


@pytest.fixture
def admin_client(env) -> TestClient:  # noqa: ANN001
    client = new_client(env[0])
    assert sign_in(client, "boss@example.com").status_code == 303
    return client


# ---- default deny


def test_every_route_refuses_a_request_with_no_session(env) -> None:  # noqa: ANN001
    app = env[0]
    public = {"/sign-in", "/health"}
    checked = 0
    # The schema is generated from the app's own routes, so a route added later is swept in automatically.
    for template, methods in app.openapi()["paths"].items():
        path = re.sub(r"\{[^}]+\}", "1", template)
        if path in public:
            continue
        for method in methods:
            response = new_client(app).request(method.upper(), path, headers=ORIGIN)
            assert response.status_code in (303, 401, 403), (method, path, response.status_code)
            checked += 1
    assert checked >= 7  # the routes were actually found


def test_a_browser_is_sent_to_the_sign_in_page_and_a_script_gets_401(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    page = client.get("/", headers={"Accept": "text/html"})
    assert page.status_code == 303 and page.headers["location"] == "/sign-in"
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Accept": "text/html", "HX-Request": "true"}).status_code == 401


def test_only_the_stylesheet_is_public_under_static(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/other.js").status_code == 401
    assert client.get("/static/../pages/account.py").status_code in (401, 404)


def test_an_unknown_host_name_is_refused(env) -> None:  # noqa: ANN001
    client = TestClient(env[0], base_url="http://evil.example", follow_redirects=False)
    assert client.get("/health").status_code == 400
    assert client.get("/sign-in").status_code == 400


def test_the_configured_name_is_accepted(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", allowed_hosts=("office-mac.local",))
    engine = upgrade(settings)
    client = TestClient(create_app(settings, engine), base_url="http://office-mac.local:8000")
    assert client.get("/health").status_code == 200
    engine.dispose()


# ---- sign in and out


def test_sign_in_sets_a_locked_down_cookie_and_opens_the_home_page(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    response = sign_in(client, "boss@example.com")
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert "po_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "secure" not in cookie.replace("samesite", "")
    home = client.get("/")
    assert home.status_code == 200 and "boss@example.com" in home.text


def test_the_cookie_is_secure_when_https_is_on(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", https=True)
    engine = upgrade(settings)
    with Session(engine) as db:
        service.create_user(db, "boss@example.com", "admin", password=PW)
    client = TestClient(create_app(settings, engine), base_url="https://127.0.0.1", follow_redirects=False)
    assert "secure" in sign_in(client, "boss@example.com").headers["set-cookie"].lower()
    engine.dispose()


def test_wrong_password_and_unknown_email_look_the_same(env) -> None:  # noqa: ANN001
    wrong = sign_in(new_client(env[0]), "boss@example.com", "not the password!")
    unknown = sign_in(new_client(env[0]), "nobody@example.com", PW)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.text.count("not right") == unknown.text.count("not right") == 1
    assert "po_session" not in wrong.headers.get("set-cookie", "")


def test_five_wrong_passwords_lock_the_account_in_the_browser_too(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    for _ in range(5):
        sign_in(client, "boss@example.com", "wrong password here")
    locked = sign_in(client, "boss@example.com", PW)
    assert locked.status_code == 401 and "Too many wrong attempts" in locked.text


def test_sign_out_ends_the_session(admin_client: TestClient) -> None:
    assert post(admin_client, "/sign-out").status_code == 303
    assert admin_client.get("/").status_code == 401


def test_the_sign_in_page_says_what_to_do_when_there_are_no_accounts(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d")
    engine = upgrade(settings)
    client = new_client(create_app(settings, engine))
    assert "create-admin" in client.get("/sign-in").text
    engine.dispose()


# ---- CSRF and Origin


def test_sign_in_needs_its_token_and_a_local_origin(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    form = client.get("/sign-in")
    good = {"email": "boss@example.com", "password": PW, "csrf_token": token_in(form.text)}
    assert client.post("/sign-in", data={**good, "csrf_token": "forged"}, headers=ORIGIN).status_code == 403
    no_token = {k: v for k, v in good.items() if k != "csrf_token"}
    assert client.post("/sign-in", data=no_token, headers=ORIGIN).status_code == 403
    assert client.post("/sign-in", data=good).status_code == 403  # no Origin
    assert client.post("/sign-in", data=good, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/sign-in", data=good, headers={"Origin": "null"}).status_code == 403
    assert client.post("/sign-in", data=good, headers=ORIGIN).status_code == 303


def test_a_signed_in_write_needs_the_session_token(admin_client: TestClient) -> None:
    for data in ({}, {"csrf_token": "forged"}):
        assert admin_client.post("/sign-out", data=data, headers=ORIGIN).status_code == 403
    page = admin_client.get("/")
    ok = {"csrf_token": token_in(page.text)}
    assert admin_client.post("/sign-out", data=ok, headers={"Origin": "https://evil.example"}).status_code == 403
    assert admin_client.post("/sign-out", data=ok).status_code == 403
    assert admin_client.post("/sign-out", data=ok, headers=ORIGIN).status_code == 303


def test_the_token_can_come_in_a_header_for_script_requests(admin_client: TestClient) -> None:
    token = token_in(admin_client.get("/").text)
    response = admin_client.post("/sign-out", headers={**ORIGIN, "X-CSRF-Token": token})
    assert response.status_code == 303


# ---- temporary passwords


def test_a_temporary_password_can_reach_nothing_until_it_is_changed(env) -> None:  # noqa: ANN001
    app, engine, _ = env
    with Session(engine) as db:
        _, temporary = service.create_user(db, "new@example.com", "buyer")
    client = new_client(app)
    signed = sign_in(client, "new@example.com", temporary or "")
    assert signed.status_code == 303 and signed.headers["location"] == "/change-password"
    assert client.get("/").headers["location"] == "/change-password"
    assert client.get("/admin/users").status_code in (303, 403)
    assert client.get("/change-password").status_code == 200
    page = client.get("/change-password")
    base = {"csrf_token": token_in(page.text), "current": temporary or ""}
    fresh = "a brand new passphrase"
    mismatch = client.post("/change-password", data={**base, "new": fresh, "again": "different"}, headers=ORIGIN)
    assert mismatch.status_code == 400 and "did not match" in mismatch.text
    weak = client.post("/change-password", data={**base, "new": "short", "again": "short"}, headers=ORIGIN)
    assert weak.status_code == 400
    done = client.post("/change-password", data={**base, "new": fresh, "again": fresh}, headers=ORIGIN)
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert client.get("/").status_code == 200
    assert sign_in(new_client(app), "new@example.com", temporary or "").status_code == 401


# ---- roles


def test_a_buyer_gets_403_on_the_admin_screens(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    sign_in(client, "buyer@example.com")
    assert client.get("/").status_code == 200
    assert client.get("/admin/users").status_code == 403
    assert post(client, "/admin/users/create", {"email": "x@example.com", "role": "admin"}).status_code == 403
    assert "People" not in client.get("/").text


BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def test_a_refusal_in_the_browser_is_a_page_not_raw_json(env) -> None:  # noqa: ANN001
    """DEF-002: a buyer typing an admin address saw {"detail":"Admins only."}."""
    client = new_client(env[0])
    sign_in(client, "buyer@example.com")
    for path in ("/admin/users", "/admin/audit"):
        page = client.get(path, headers=BROWSER)
        assert page.status_code == 403 and page.headers["content-type"].startswith("text/html")
        assert "<h1>Admins only</h1>" in page.text and "Go to Home" in page.text and '{"detail"' not in page.text
        assert 'href="/orders"' in page.text and 'href="/admin/users"' not in page.text  # the buyer's own top bar
    htmx = client.get("/admin/users", headers={"HX-Request": "true"})
    assert htmx.status_code == 403 and "<h1>Admins only</h1>" in htmx.text


def test_a_script_still_gets_the_short_json_answer(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    sign_in(client, "buyer@example.com")
    answer = client.get("/admin/users", headers={"Accept": "application/json"})
    assert answer.status_code == 403 and answer.json() == {"detail": "Admins only."}


def test_a_mistyped_address_and_an_unreadable_form_are_pages_too(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    sign_in(client, "buyer@example.com")
    missing = client.get("/no-such-page", headers=BROWSER)
    assert missing.status_code == 404 and "<h1>Page not found</h1>" in missing.text
    token = token_in(client.get("/vendors/new").text)
    unreadable = client.post("/vendors/create", headers={**ORIGIN, **BROWSER}, data={"csrf_token": token})
    assert unreadable.status_code == 422 and "<h1>The form could not be read</h1>" in unreadable.text
    wrong_method = client.put("/vendors", headers={**ORIGIN, **BROWSER, "X-CSRF-Token": token})
    assert wrong_method.status_code == 405 and "<h1>That cannot be done here</h1>" in wrong_method.text


def test_a_demotion_applies_on_the_very_next_request(env, admin_client: TestClient) -> None:  # noqa: ANN001
    app, engine, _ = env
    second = new_client(app)
    with Session(engine) as db:
        service.create_user(db, "two@example.com", "admin", password=PW)
    sign_in(second, "two@example.com")
    assert second.get("/admin/users").status_code == 200
    with Session(engine) as db:
        user = service.find_user(db, "two@example.com")
        service.set_role(db, user, "buyer")  # type: ignore[arg-type]
    assert second.get("/admin/users").status_code == 401  # the session ended with the change


def test_a_disabled_person_is_cut_off_at_once(env, admin_client: TestClient) -> None:  # noqa: ANN001
    app, engine, _ = env
    buyer = new_client(app)
    sign_in(buyer, "buyer@example.com")
    with Session(engine) as db:
        user = service.find_user(db, "buyer@example.com")
        service.disable(db, user)  # type: ignore[arg-type]
    assert buyer.get("/").status_code == 401


def test_admin_adds_a_person_and_sees_the_temporary_password_once(admin_client: TestClient, env) -> None:  # noqa: ANN001
    response = post(admin_client, "/admin/users/create", {"email": "new@example.com", "role": "buyer"})
    assert response.status_code == 303 and response.headers["location"] == "/admin/users"
    assert "Temporary password" not in response.text  # nothing secret in the answer to the form post
    shown = admin_client.get("/admin/users")
    assert "Temporary password for" in shown.text
    secret = re.search(r'class="secret">([^<]+)<', shown.text).group(1)  # type: ignore[union-attr]
    assert len(secret) == 20
    assert secret not in admin_client.get("/admin/users").text  # Reload shows it no more
    assert sign_in(new_client(env[0]), "new@example.com", secret).headers["location"] == "/change-password"


def test_admin_screens_show_errors_not_crashes(admin_client: TestClient) -> None:
    dup = post(admin_client, "/admin/users/create", {"email": "buyer@example.com", "role": "buyer"})
    assert dup.status_code == 400 and "already has an account" in dup.text
    assert post(admin_client, "/admin/users/999/reset").status_code == 404
    page = admin_client.get("/admin/users")
    boss_id = re.search(r"/admin/users/(\d+)/disable", page.text).group(1)  # type: ignore[union-attr]
    own = post(admin_client, f"/admin/users/{boss_id}/disable")
    assert own.status_code == 400


def test_the_last_admin_cannot_be_demoted_from_the_screen(admin_client: TestClient) -> None:
    page = admin_client.get("/admin/users")
    boss_id = re.search(r"/admin/users/(\d+)/role", page.text).group(1)  # type: ignore[union-attr]
    response = post(admin_client, f"/admin/users/{boss_id}/role", {"role": "buyer"})
    assert response.status_code == 400 and "last admin" in response.text


# ---- headers


@pytest.mark.parametrize("path", ["/sign-in", "/health", "/static/app.css"])
def test_security_headers_are_on_public_responses(env, path: str) -> None:  # noqa: ANN001
    response = new_client(env[0]).get(path)
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


def test_signed_in_pages_are_not_cached_and_refusals_carry_headers(admin_client: TestClient, env) -> None:  # noqa: ANN001
    assert admin_client.get("/").headers["cache-control"] == "no-store"
    refused = new_client(env[0]).get("/")
    assert refused.status_code == 401 and "content-security-policy" in refused.headers


def test_no_page_has_inline_script_or_style(admin_client: TestClient) -> None:
    for path in ("/", "/admin/users", "/change-password", "/sign-in"):
        html = admin_client.get(path).text
        assert not INLINE_SCRIPT.search(html) and "<style" not in html and ' style="' not in html, path


# ---- PD-1.9d: the remaining security tests from ARCHITECTURE §10.1


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", 200), ("127.0.0.1:8000", 200), ("localhost:8000", 200), ("LOCALHOST", 200),
        ("[::1]:8000", 200), ("[::1]", 200),
        ("evil.example", 400), ("127.0.0.1.evil.example", 400), ("localhost.evil.example:8000", 400),
        ("", 400), ("127.0.0.1:8000@evil.example", 400),
    ],
)  # fmt: skip
def test_the_host_check_handles_ports_ipv6_and_lookalikes(env, host: str, expected: int) -> None:  # noqa: ANN001
    client = TestClient(env[0], base_url="http://127.0.0.1", follow_redirects=False)
    assert client.get("/health", headers={"Host": host}).status_code == expected


def test_a_forged_or_malformed_session_cookie_is_just_no_session(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    for cookie in ("po_session=forged", "po_session=", "po_session=a; po_session=b", "\x00\x01garbage", "po_session"):
        assert client.get("/", headers={"Cookie": cookie}).status_code == 401, cookie


def test_an_oversized_form_is_refused_not_buffered_forever(env) -> None:  # noqa: ANN001
    client = new_client(env[0])
    form = client.get("/sign-in")
    big = {"email": "a@example.com", "password": "x" * 100_000, "csrf_token": token_in(form.text)}
    assert client.post("/sign-in", data=big, headers=ORIGIN).status_code == 403


def test_the_session_token_changes_at_each_stage(env) -> None:  # noqa: ANN001
    app, engine, _ = env
    with Session(engine) as db:
        _, temporary = service.create_user(db, "new@example.com", "buyer")
    client = new_client(app)
    first = sign_in(client, "new@example.com", temporary or "").cookies.get("po_session")
    page = client.get("/change-password")
    done = client.post(
        "/change-password",
        data={"csrf_token": token_in(page.text), "current": temporary or "", "new": "a brand new passphrase",
              "again": "a brand new passphrase"},
        headers=ORIGIN,
    )  # fmt: skip
    second = done.cookies.get("po_session")
    assert first and second and first != second
    stale = new_client(app)
    assert stale.get("/", headers={"Cookie": f"po_session={first}"}).status_code == 401


def test_a_new_sign_in_never_reuses_a_token(env) -> None:  # noqa: ANN001
    tokens = {sign_in(new_client(env[0]), "boss@example.com").cookies.get("po_session") for _ in range(3)}
    assert len(tokens) == 3 and None not in tokens


def test_no_secret_is_in_the_database_file_or_the_logs(env, caplog: pytest.LogCaptureFixture, capsys) -> None:  # noqa: ANN001
    app, engine, settings = env
    caplog.set_level("DEBUG")
    client = new_client(app)
    resp = sign_in(client, "boss@example.com")
    session_token = resp.cookies.get("po_session") or ""
    post(client, "/admin/users/create", {"email": "new@example.com", "role": "buyer"})
    temporary = re.search(r'class="secret">([^<]+)<', client.get("/admin/users").text).group(1)  # type: ignore[union-attr]
    wrong = "a wrong guess that must not leak"
    sign_in(new_client(app), "boss@example.com", wrong)
    csrf = token_in(client.get("/").text)
    engine.dispose()
    data = b"".join(p.read_bytes() for p in settings.data_dir.iterdir() if p.name.startswith("po-dashboard.db"))
    seen = caplog.text + capsys.readouterr().out + capsys.readouterr().err
    for secret in (PW, wrong, temporary, session_token):
        assert secret.encode() not in data, "found in the database files"
    # The CSRF token is stored on purpose: it is useless without the session cookie, which only the browser holds.
    for secret in (PW, wrong, temporary, session_token, csrf):
        assert secret not in seen, "found in a log or the console"
    assert b"$argon2id$" in data  # hashes are there, secrets are not


def test_the_data_folder_and_database_stay_owner_only_after_use(env) -> None:  # noqa: ANN001
    import stat

    app, engine, settings = env
    sign_in(new_client(app), "boss@example.com")
    assert stat.S_IMODE(settings.data_dir.stat().st_mode) == 0o700
    for p in settings.data_dir.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) & 0o077 == 0, p.name


def test_a_get_never_changes_anything(env, admin_client: TestClient) -> None:  # noqa: ANN001
    _, engine, _ = env
    from sqlalchemy import func, select

    from po_dashboard.db.models import AppUser, AuditLog

    def snapshot() -> tuple[int, int]:
        with Session(engine) as db:
            return db.scalar(select(func.count(AppUser.id))) or 0, db.scalar(select(func.count(AuditLog.id))) or 0

    before = snapshot()
    for path in ("/", "/admin/users", "/change-password", "/sign-in", "/health"):
        admin_client.get(path)
    for path in ("/sign-out", "/admin/users/1/disable", "/admin/users/create"):
        assert admin_client.get(path).status_code in (404, 405)
    assert snapshot() == before


def test_a_reload_after_adding_a_person_repeats_nothing(admin_client: TestClient) -> None:
    post(admin_client, "/admin/users/create", {"email": "once@example.com", "role": "buyer"})
    assert "Temporary password for" in admin_client.get("/admin/users").text
    again = admin_client.get("/admin/users")  # what Reload now does
    assert again.status_code == 200 and "Temporary password for" not in again.text and "already has" not in again.text


def test_the_shown_password_expires_and_belongs_to_one_admin(admin_client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    from po_dashboard.pages import admin as admin_page

    post(admin_client, "/admin/users/create", {"email": "slow@example.com", "role": "buyer"})
    later = admin_page.time.monotonic() + admin_page.SHOWN_FOR_SECONDS + 1
    monkeypatch.setattr(admin_page.time, "monotonic", lambda: later)
    assert "Temporary password for" not in admin_client.get("/admin/users").text


# ---- look and feel rules (PD-1.9e)


def test_every_page_carries_the_product_name(env, admin_client: TestClient) -> None:  # noqa: ANN001
    sign_in_page = new_client(env[0]).get("/sign-in", headers={"Accept": "text/html"})
    assert "<title>Sign in · RaceSpec Internal Dashboard</title>" in sign_in_page.text
    for path in ("/", "/admin/users", "/change-password"):
        assert "RaceSpec Internal Dashboard</title>" in admin_client.get(path).text


def test_the_cursor_is_ready_in_the_first_box_of_every_form_page(env, admin_client: TestClient) -> None:  # noqa: ANN001
    pages = {
        "/sign-in": new_client(env[0]).get("/sign-in").text,
        "/change-password": admin_client.get("/change-password").text,
        "/admin/users": admin_client.get("/admin/users").text,
    }
    for path, html in pages.items():
        assert html.count(" autofocus") == 1, f"{path} must focus exactly one field"
    first_input = re.search(r'<input id="(\w+)"[^>]*autofocus', pages["/change-password"])
    assert first_input and first_input.group(1) == "current"


def test_pages_use_no_inline_style_or_script(env, admin_client: TestClient) -> None:  # noqa: ANN001
    """The Content-Security-Policy forbids both, so a page that used one would silently break."""
    for path in ("/", "/admin/users", "/change-password"):
        html = admin_client.get(path).text
        assert " style=" not in html and not INLINE_SCRIPT.search(html) and " onclick=" not in html, path
    assert " style=" not in new_client(env[0]).get("/sign-in").text


def test_logo_files_are_public_and_the_page_works_without_them(env) -> None:  # noqa: ANN001
    app = env[0]
    client = new_client(app)
    brand = app.state.templates.env.globals
    if brand["has_logo"]:
        assert client.get("/static/brand/logo.png").status_code == 200
    brand["has_logo"] = False
    page = client.get("/sign-in").text
    assert "<img" not in page and "RaceSpec" in page
