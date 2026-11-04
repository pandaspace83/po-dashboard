"""Password hashing and rules. argon2id; at most two hashes at once so a burst cannot exhaust memory (§10.1)."""

from __future__ import annotations

import secrets
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 128
TEMP_PASSWORD_LENGTH = 20

# A short list of the most common passwords. Length and the email check do most of the work.
COMMON = frozenset(
    p.lower() for p in (
        "password1234", "123456789012", "qwertyuiop12", "iloveyou1234", "administrator", "welcome12345",
        "letmein12345", "changeme1234", "passwordpassword", "123456123456", "1234567890123", "purchasing123",
        "password12345", "qwerty123456", "abc123abc123", "welcome1234567", "p@ssw0rd1234", "adminadmin12",
    )
)  # fmt: skip

hasher = PasswordHasher()
_slots = threading.BoundedSemaphore(2)
_dummy: str | None = None


class PasswordRejected(ValueError):
    """The password breaks a rule. The message says which, and is safe to show."""


def check_rules(password: str, email: str) -> None:
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise PasswordRejected(f"Use {MIN_LENGTH} to {MAX_LENGTH} characters.")
    lowered = password.lower()
    local_part = email.split("@", 1)[0].lower()
    if email.lower() in lowered or (len(local_part) >= 4 and local_part in lowered):
        raise PasswordRejected("The password must not contain your email address.")
    if lowered in COMMON:
        raise PasswordRejected("That password is too common.")


def hash_password(password: str) -> str:
    with _slots:
        return hasher.hash(password)


def verify(stored_hash: str, password: str) -> bool:
    try:
        with _slots:
            return hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return hasher.check_needs_rehash(stored_hash)


def burn_time(password: str) -> None:
    """Hash against a dummy so an unknown email takes as long as a wrong password."""
    global _dummy
    if _dummy is None:
        _dummy = hash_password(secrets.token_urlsafe(16))
    verify(_dummy, password)


def new_temporary_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"  # no look-alike characters
    return "".join(secrets.choice(alphabet) for _ in range(TEMP_PASSWORD_LENGTH))
