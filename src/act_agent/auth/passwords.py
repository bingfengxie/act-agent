from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_ph = PasswordHasher()

# Constant-time-ish dummy hash so unknown users and wrong passwords cost the same.
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def dummy_verify(password: str) -> None:
    try:
        _ph.verify(_DUMMY_HASH, password)
    except Exception:  # noqa: BLE001 - timing equaliser, result intentionally ignored
        pass
