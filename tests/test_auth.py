from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from po_dashboard.auth import passwords, service
from po_dashboard.config import Settings
from po_dashboard.db.migrate import upgrade
from po_dashboard.db.models import AppUser, AuditLog, SignInSession

PW = "correct horse battery"
T0 = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = upgrade(Settings(data_dir=tmp_path / "data"))
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Session:
    with Session(engine) as session:
        yield session


@pytest.fixture
def admin(db: Session) -> AppUser:
    return service.create_user(db, "boss@example.com", "admin", password=PW, now=T0)[0]


def actions(db: Session) -> list[str]:
    return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id)).all()]


# ---- password rules and storage


def test_the_password_is_stored_as_an_argon2id_hash_only(db: Session, admin: AppUser) -> None:
    assert admin.password_hash.startswith("$argon2id$") and PW not in admin.password_hash


@pytest.mark.parametrize("bad", ["short", "x" * 129, "boss@example.com1234", "bossbossboss", "administrator"])
def test_weak_passwords_are_refused(bad: str) -> None:
    with pytest.raises(passwords.PasswordRejected):
        passwords.check_rules(bad, "boss@example.com")


def test_a_good_passphrase_is_accepted() -> None:
    passwords.check_rules(PW, "boss@example.com")


def test_temporary_passwords_are_long_and_random() -> None:
    a, b = passwords.new_temporary_password(), passwords.new_temporary_password()
    assert len(a) == 20 and a != b


# ---- sign in


def test_sign_in_starts_a_full_session_and_stores_only_the_token_hash(db: Session, admin: AppUser) -> None:
    result = service.sign_in(db, "  BOSS@example.com ", PW, now=T0)
    assert result.session.stage == "full"
    stored = db.scalars(select(SignInSession)).one()
    assert result.token not in (stored.token_hash, stored.csrf_token) and len(stored.token_hash) == 64
    assert "sign_in" in actions(db)


def test_wrong_password_and_unknown_email_get_the_same_refusal(db: Session, admin: AppUser) -> None:
    with pytest.raises(service.InvalidCredentials) as wrong:
        service.sign_in(db, "boss@example.com", "not the password!", now=T0)
    with pytest.raises(service.InvalidCredentials) as unknown:
        service.sign_in(db, "nobody@example.com", PW, now=T0)
    assert str(wrong.value) == str(unknown.value)


def test_a_failed_attempt_on_an_unknown_account_does_not_store_what_was_typed(db: Session) -> None:
    secret_typed_in_email_box = "hunter2hunter2@example.com"
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(db, secret_typed_in_email_box, "x", now=T0)
    entry = db.scalars(select(AuditLog)).one()
    assert entry.action == "sign_in_failed" and entry.object == "unknown account"
    assert "hunter2" not in " ".join(str(v) for v in (entry.object, entry.before, entry.after))


def test_five_failures_lock_the_account_even_for_the_right_password(db: Session, admin: AppUser) -> None:
    for _ in range(5):
        with pytest.raises(service.InvalidCredentials):
            service.sign_in(db, admin.email, "wrong password here", now=T0)
    with pytest.raises(service.Locked):
        service.sign_in(db, admin.email, PW, now=T0 + timedelta(minutes=14))
    assert "account_locked" in actions(db)
    assert service.sign_in(db, admin.email, PW, now=T0 + timedelta(minutes=16)).session.stage == "full"


def test_a_success_resets_the_failure_count(db: Session, admin: AppUser) -> None:
    for _ in range(4):
        with pytest.raises(service.InvalidCredentials):
            service.sign_in(db, admin.email, "wrong password here", now=T0)
    service.sign_in(db, admin.email, PW, now=T0)
    assert admin.failed_attempts == 0
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(db, admin.email, "wrong password here", now=T0)
    assert admin.locked_until is None


def test_a_disabled_account_gets_the_ordinary_refusal(db: Session, admin: AppUser) -> None:
    other = service.create_user(db, "b@example.com", "buyer", password=PW)[0]
    service.disable(db, other, actor=admin)
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(db, other.email, PW)


# ---- sessions


def test_idle_and_absolute_expiry(db: Session, admin: AppUser) -> None:
    token = service.sign_in(db, admin.email, PW, now=T0).token
    assert service.resolve_session(db, token, now=T0 + timedelta(minutes=29)) is not None
    assert service.resolve_session(db, token, now=T0 + timedelta(minutes=29 + 31)) is None  # idle
    token = service.sign_in(db, admin.email, PW, now=T0).token
    for step in range(1, 30):  # active every 29 minutes: still cut off at 12 hours
        alive = service.resolve_session(db, token, now=T0 + timedelta(minutes=29 * step))
        if T0 + timedelta(minutes=29 * step) >= T0 + service.ABSOLUTE:
            assert alive is None
            break
    else:
        pytest.fail("the session never expired")


def test_an_unknown_or_empty_token_resolves_to_nothing(db: Session, admin: AppUser) -> None:
    assert service.resolve_session(db, "nonsense") is None
    assert service.resolve_session(db, None) is None


def test_sign_out_ends_the_session(db: Session, admin: AppUser) -> None:
    result = service.sign_in(db, admin.email, PW, now=T0)
    service.sign_out(db, result.session)
    assert service.resolve_session(db, result.token, now=T0) is None


def test_purge_removes_expired_sessions(db: Session, admin: AppUser) -> None:
    service.sign_in(db, admin.email, PW, now=T0)
    assert service.purge_expired(db, now=T0 + service.ABSOLUTE + timedelta(seconds=1)) == 1


# ---- temporary passwords and people


def test_a_temporary_password_gives_only_a_short_pending_session(db: Session, admin: AppUser) -> None:
    user, temporary = service.create_user(db, "new@example.com", "buyer", actor=admin, now=T0)
    assert temporary is not None and user.must_change_password
    result = service.sign_in(db, user.email, temporary, now=T0)
    assert result.session.stage == "pending"
    assert result.session.expires_at == T0 + service.PENDING


def test_an_expired_temporary_password_is_refused(db: Session, admin: AppUser) -> None:
    user, temporary = service.create_user(db, "new@example.com", "buyer", actor=admin, now=T0)
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(db, user.email, temporary or "", now=T0 + service.TEMP_PASSWORD_LIFETIME)


def test_changing_the_password_ends_other_sessions_and_clears_the_flag(db: Session, admin: AppUser) -> None:
    user, temporary = service.create_user(db, "new@example.com", "buyer", actor=admin, now=T0)
    first = service.sign_in(db, user.email, temporary or "", now=T0)
    other = service.sign_in(db, user.email, temporary or "", now=T0)
    service.change_password(db, user, temporary or "", "a brand new passphrase", keep_session=first.session.id)
    assert not user.must_change_password
    assert service.resolve_session(db, other.token, now=T0) is None
    assert service.sign_in(db, user.email, "a brand new passphrase", now=T0).session.stage == "full"


def test_change_password_needs_the_current_one_and_a_different_new_one(db: Session, admin: AppUser) -> None:
    with pytest.raises(service.InvalidCredentials):
        service.change_password(db, admin, "wrong current one", "a brand new passphrase")
    with pytest.raises(passwords.PasswordRejected):
        service.change_password(db, admin, PW, PW)


def test_reset_password_ends_sessions_unlocks_and_forces_a_change(db: Session, admin: AppUser) -> None:
    result = service.sign_in(db, admin.email, PW, now=T0)
    admin.locked_until = T0 + timedelta(minutes=10)
    temporary = service.reset_password(db, admin, now=T0)
    assert service.resolve_session(db, result.token, now=T0) is None
    assert admin.locked_until is None and admin.must_change_password
    assert service.sign_in(db, admin.email, temporary, now=T0).session.stage == "pending"


def test_disabling_and_demoting_end_sessions_at_once(db: Session, admin: AppUser) -> None:
    second = service.create_user(db, "two@example.com", "admin", password=PW)[0]
    token = service.sign_in(db, second.email, PW, now=T0).token
    service.set_role(db, second, "buyer", actor=admin)
    assert service.resolve_session(db, token, now=T0) is None
    token = service.sign_in(db, second.email, PW, now=T0).token
    service.disable(db, second, actor=admin)
    assert service.resolve_session(db, token, now=T0) is None


def test_the_last_admin_cannot_be_disabled_or_demoted(db: Session, admin: AppUser) -> None:
    with pytest.raises(service.AuthError, match="last admin"):
        service.disable(db, admin)
    with pytest.raises(service.AuthError, match="last admin"):
        service.set_role(db, admin, "buyer")


def test_an_admin_cannot_disable_themselves(db: Session, admin: AppUser) -> None:
    service.create_user(db, "two@example.com", "admin", password=PW)
    with pytest.raises(service.AuthError, match="own account"):
        service.disable(db, admin, actor=admin)


def test_bad_emails_roles_and_duplicates_are_refused(db: Session, admin: AppUser) -> None:
    for bad in ("", "no-at-sign", "a@b", "a b@example.com"):
        with pytest.raises(service.AuthError):
            service.create_user(db, bad, "buyer", password=PW)
    with pytest.raises(service.AuthError, match="role"):
        service.create_user(db, "x@example.com", "owner", password=PW)
    with pytest.raises(service.AuthError, match="already"):
        service.create_user(db, "BOSS@example.com", "buyer", password=PW)


def test_nothing_secret_reaches_the_audit_trail(db: Session, admin: AppUser) -> None:
    user, temporary = service.create_user(db, "new@example.com", "buyer", actor=admin)
    service.sign_in(db, user.email, temporary or "")
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(db, user.email, "a wrong guess here")
    text = " ".join(f"{a.object} {a.before} {a.after} {a.action}" for a in db.scalars(select(AuditLog)).all())
    for secret in (PW, temporary or "", "a wrong guess here", "$argon2"):
        assert secret not in text
