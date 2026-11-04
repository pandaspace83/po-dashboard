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
    assert response.status_code == 200 and "Temporary password for" in response.text
    secret = re.search(r'class="secret">([^<]+)<', response.text).group(1)  # type: ignore[union-attr]
    assert len(secret) == 20
    assert secret not in admin_client.get("/admin/users").text
    assert sign_in(new_client(env[0]), "new@example.com", secret).headers["location"] == "/change-password"


def test_admin_screens_show_errors_not_crashes(admin_client: TestClient) -> None:
    dup = post(admin_client, "/admin/users/create", {"email": "buyer@example.com", "role": "buyer"})
    assert dup.status_code == 400 and "already has an account" in dup.text
    assert post(admin_client, "/admin/users/999/reset").status_code == 404
    page = admin_client.get("/admin/users")
    boss_id = re.search(r'/admin/users/(\d+)/disable', page.text).group(1)  # type: ignore[union-attr]
    own = post(admin_client, f"/admin/users/{boss_id}/disable")
    assert own.status_code == 400


def test_the_last_admin_cannot_be_demoted_from_the_screen(admin_client: TestClient) -> None:
    page = admin_client.get("/admin/users")
    boss_id = re.search(r'/admin/users/(\d+)/role', page.text).group(1)  # type: ignore[union-attr]
    response = post(admin_client, f"/admin/users/{boss_id}/role", {"role": "buyer"})
    assert response.status_code == 400 and "last admin" in response.text


# ---- headers


@pytest.mark.parametrize("path", ["/sign-in", "/health", "/static/app.css"])
def test_security_headers_are_on_public_responses(env, path: str) -> None:  # noqa: ANN001
    response = new_client(env[0]).get(path)
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_signed_in_pages_are_not_cached_and_refusals_carry_headers(admin_client: TestClient, env) -> None:  # noqa: ANN001
    assert admin_client.get("/").headers["cache-control"] == "no-store"
    refused = new_client(env[0]).get("/")
    assert refused.status_code == 401 and "content-security-policy" in refused.headers


def test_no_page_has_inline_script_or_style(admin_client: TestClient) -> None:
    for path in ("/", "/admin/users", "/change-password", "/sign-in"):
        html = admin_client.get(path).text
        assert "<script" not in html and "<style" not in html and ' style="' not in html, path
