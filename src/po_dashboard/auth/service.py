"""Sign-in rules (ARCHITECTURE §10.1): lockout, sessions, people. No web code here, so it is tested directly.

The second factor is paused (DEC-34). Until it exists the app refuses network access (config.py), and a session is
`full` straight after the password, or `pending` while a temporary password must be changed.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth import passwords
from po_dashboard.db.base import utcnow
from po_dashboard.db.models import AppUser, AuditLog, SignInSession

LOCKOUT_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
IDLE = timedelta(minutes=30)
ABSOLUTE = timedelta(hours=12)
PENDING = timedelta(minutes=10)
TEMP_PASSWORD_LIFETIME = timedelta(hours=72)
ROLES = ("admin", "buyer")


class AuthError(Exception):
    """A refusal whose message is safe to show the person."""


class InvalidCredentials(AuthError):
    """Same message for an unknown email, a wrong password, a disabled account and an expired temporary password."""

    def __init__(self) -> None:
        super().__init__("The email or password is not right.")


class Locked(AuthError):
    def __init__(self, until: datetime, now: datetime) -> None:
        minutes = max(1, int((until - now).total_seconds() // 60) + 1)
        super().__init__(f"Too many wrong attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.")
        self.until = until


@dataclass
class SignedIn:
    token: str  # shown to the browser once; only its hash is stored
    session: SignInSession
    user: AppUser


def normalise_email(email: str) -> str:
    cleaned = email.strip().lower()
    local, _, domain = cleaned.partition("@")
    if not local or "." not in domain or " " in cleaned or len(cleaned) > 254:
        raise AuthError("Enter a valid email address.")
    return cleaned


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def audit(
    db: Session, action: str, user: AppUser | None = None, obj: str | None = None,
    client_address: str | None = None, before: object = None, after: object = None,
) -> None:  # fmt: skip
    """Never pass a password, token or code here. Values are stored as given."""
    db.add(
        AuditLog(
            user_id=user.id if user else None, client_address=client_address, action=action, object=obj,
            before=None if before is None else json.dumps(before), after=None if after is None else json.dumps(after),
        )  # fmt: skip
    )


def find_user(db: Session, email: str) -> AppUser | None:
    return db.scalar(select(AppUser).where(AppUser.email == email.strip().lower()))


def _start_session(db: Session, user: AppUser, now: datetime) -> SignedIn:
    stage = "pending" if user.must_change_password else "full"
    token = secrets.token_urlsafe(32)
    row = SignInSession(
        user_id=user.id, token_hash=token_hash(token), stage=stage, csrf_token=secrets.token_urlsafe(32),
        created_at=now, last_seen_at=now, expires_at=now + (PENDING if stage == "pending" else ABSOLUTE),
    )  # fmt: skip
    db.add(row)
    db.flush()
    return SignedIn(token, row, user)


def sign_in(
    db: Session, email: str, password: str, *, client_address: str | None = None, now: datetime | None = None
) -> SignedIn:
    now = now or utcnow()
    user = find_user(db, email)
    if user is not None and user.locked_until is not None and user.locked_until > now:
        # The password is not checked while locked, so a right guess reveals nothing.
        audit(db, "sign_in_refused_locked", user, client_address=client_address)
        db.commit()
        raise Locked(user.locked_until, now)
    usable = user is not None and user.disabled_at is None
    if usable and user is not None:
        ok = passwords.verify(user.password_hash, password)
    else:
        passwords.burn_time(password)
        ok = False
    if ok and user is not None and user.must_change_password and (
        user.temp_password_expires_at is not None and user.temp_password_expires_at <= now
    ):
        ok = False
    if not ok or user is None:
        if user is not None and usable:
            user.failed_attempts += 1
            if user.failed_attempts >= LOCKOUT_ATTEMPTS:
                user.locked_until = now + LOCKOUT
                user.failed_attempts = 0
                audit(db, "account_locked", user, client_address=client_address)
        # An unknown account is recorded as that. The typed text is not stored:
        # people sometimes type a password into the email box.
        audit(db, "sign_in_failed", user, obj=None if user else "unknown account", client_address=client_address)
        db.commit()
        raise InvalidCredentials
    user.failed_attempts = 0
    user.locked_until = None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    result = _start_session(db, user, now)
    audit(db, "sign_in", user, client_address=client_address)
    db.commit()
    return result


def resolve_session(db: Session, token: str | None, *, now: datetime | None = None) -> SignInSession | None:
    """The live session for a browser's token, or None. Applies idle and absolute expiry and refreshes last_seen."""
    if not token:
        return None
    now = now or utcnow()
    row = db.scalar(select(SignInSession).where(SignInSession.token_hash == token_hash(token)))
    if row is None:
        return None
    user = db.get(AppUser, row.user_id)
    expired = now >= row.expires_at or (row.stage == "full" and now >= row.last_seen_at + IDLE)
    if expired or user is None or user.disabled_at is not None:
        db.delete(row)
        db.commit()
        return None
    row.last_seen_at = now
    db.commit()
    return row


def sign_out(db: Session, session: SignInSession, *, client_address: str | None = None) -> None:
    user = db.get(AppUser, session.user_id)
    db.delete(session)
    audit(db, "sign_out", user, client_address=client_address)
    db.commit()


def end_sessions(db: Session, user: AppUser, *, keep: int | None = None) -> None:
    for row in db.scalars(select(SignInSession).where(SignInSession.user_id == user.id)).all():
        if row.id != keep:
            db.delete(row)


def purge_expired(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    stale = db.scalars(select(SignInSession).where(SignInSession.expires_at <= now)).all()
    for row in stale:
        db.delete(row)
    db.commit()
    return len(stale)


# ---- people ----


def _enabled_admins(db: Session) -> list[AppUser]:
    return list(db.scalars(select(AppUser).where(AppUser.role == "admin", AppUser.disabled_at.is_(None))).all())


def _require_another_admin(db: Session, user: AppUser) -> None:
    others = [a for a in _enabled_admins(db) if a.id != user.id]
    if user.role == "admin" and user.disabled_at is None and not others:
        raise AuthError("This is the last admin. Make someone else an admin first.")


def create_user(
    db: Session, email: str, role: str, *, password: str | None = None,
    actor: AppUser | None = None, now: datetime | None = None,
) -> tuple[AppUser, str | None]:  # fmt: skip
    """Add a person. With no password, a temporary one is made, returned once, and must be changed at first use."""
    now = now or utcnow()
    email = normalise_email(email)
    if role not in ROLES:
        raise AuthError("The role must be admin or buyer.")
    if find_user(db, email) is not None:
        raise AuthError("That email already has an account.")
    temporary = password is None
    if password is None:
        password = passwords.new_temporary_password()
    else:
        passwords.check_rules(password, email)
    user = AppUser(
        email=email, password_hash=passwords.hash_password(password), role=role, must_change_password=temporary,
        temp_password_expires_at=now + TEMP_PASSWORD_LIFETIME if temporary else None, created_at=now,
    )  # fmt: skip
    db.add(user)
    db.flush()
    audit(db, "user_created", actor, obj=email, after={"role": role, "temporary_password": temporary})
    db.commit()
    return user, password if temporary else None


def change_password(
    db: Session, user: AppUser, current: str, new: str, *, keep_session: int | None = None, actor: AppUser | None = None
) -> None:  # fmt: skip
    if not passwords.verify(user.password_hash, current):
        audit(db, "password_change_refused", user)
        db.commit()
        raise InvalidCredentials
    passwords.check_rules(new, user.email)
    if passwords.verify(user.password_hash, new):
        raise passwords.PasswordRejected("Choose a password you have not used for this account.")
    user.password_hash = passwords.hash_password(new)
    user.must_change_password = False
    user.temp_password_expires_at = None
    end_sessions(db, user, keep=keep_session)
    audit(db, "password_changed", actor or user, obj=user.email)
    db.commit()


def reset_password(db: Session, user: AppUser, *, actor: AppUser | None = None, now: datetime | None = None) -> str:
    """A new temporary password, returned once. Ends the person's sessions and clears any lockout."""
    now = now or utcnow()
    temporary = passwords.new_temporary_password()
    user.password_hash = passwords.hash_password(temporary)
    user.must_change_password = True
    user.temp_password_expires_at = now + TEMP_PASSWORD_LIFETIME
    user.failed_attempts = 0
    user.locked_until = None
    end_sessions(db, user)
    audit(db, "password_reset", actor, obj=user.email)
    db.commit()
    return temporary


def unlock(db: Session, user: AppUser, *, actor: AppUser | None = None) -> None:
    user.failed_attempts = 0
    user.locked_until = None
    audit(db, "account_unlocked", actor, obj=user.email)
    db.commit()


def disable(db: Session, user: AppUser, *, actor: AppUser | None = None, now: datetime | None = None) -> None:
    if actor is not None and actor.id == user.id:
        raise AuthError("You cannot disable your own account.")
    _require_another_admin(db, user)
    user.disabled_at = now or utcnow()
    end_sessions(db, user)
    audit(db, "user_disabled", actor, obj=user.email)
    db.commit()


def enable(db: Session, user: AppUser, *, actor: AppUser | None = None) -> None:
    user.disabled_at = None
    audit(db, "user_enabled", actor, obj=user.email)
    db.commit()


def set_role(db: Session, user: AppUser, role: str, *, actor: AppUser | None = None) -> None:
    if role not in ROLES:
        raise AuthError("The role must be admin or buyer.")
    if role != "admin":
        _require_another_admin(db, user)
    before = user.role
    user.role = role
    end_sessions(db, user)
    audit(db, "role_changed", actor, obj=user.email, before=before, after=role)
    db.commit()
