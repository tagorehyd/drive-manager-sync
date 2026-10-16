"""Single admin account: bcrypt password hash, signed session cookies with epoch invalidation, lockout."""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
import time
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

from app import config

log = logging.getLogger(__name__)

COOKIE_NAME = "dm_session"
MIN_PASSWORD_LENGTH = 8
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
SETUP_PASSWORD_FILE = config.CONFIG_DIR / "setup-password"

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def session_max_age() -> int:
    try:
        days = int(os.environ.get("SESSION_DAYS", "7"))
    except ValueError:
        days = 7
    return max(1, days) * 86400


def cookie_secure() -> bool:
    return cookie_samesite() == "none" or os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")


def cookie_samesite() -> str:
    """'none' lets cross-site requests send the session cookie (browsers then require HTTPS)."""
    value = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower()
    return value if value in ("lax", "strict", "none") else "lax"


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return _pwd.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def is_setup_complete() -> bool:
    data = config.load("auth")
    return bool(data.get("setup_completed") and data.get("password_hash"))


def validate_new_password(password: str, confirm: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password != confirm:
        raise ValueError("Passwords do not match")


def complete_setup(username: str, password: str, confirm: str, setup_pw: str) -> None:
    if is_setup_complete():
        raise ValueError("Setup already completed")
    if lockout.remaining():
        raise TimeoutError(lockout.remaining())
    if not hmac.compare_digest(str(setup_pw or "").strip(), setup_password()):
        locked_for = lockout.fail()
        if locked_for:
            raise TimeoutError(locked_for)
        raise PermissionError("Invalid setup password (see container logs or /config/setup-password)")
    lockout.success()
    username = (username or "admin").strip() or "admin"
    validate_new_password(password, confirm)

    def apply(data: dict[str, Any]) -> None:
        data.update(setup_completed=True, username=username, password_hash=hash_password(password),
                    session_epoch=max(1, int(data.get("session_epoch") or 1)))
    config.update("auth", apply)
    SETUP_PASSWORD_FILE.unlink(missing_ok=True)


def setup_password() -> str:
    """One-time first-boot password guarding /setup: $SETUP_PASSWORD or a generated /config/setup-password."""
    env = os.environ.get("SETUP_PASSWORD", "").strip()
    if env:
        return env
    try:
        value = SETUP_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(12)
    config.atomic_write_text(SETUP_PASSWORD_FILE, value + "\n", mode=0o600)
    return value


def change_password(old_password: str, new_password: str, confirm: str) -> None:
    data = config.load("auth")
    if not verify_password(old_password, data.get("password_hash", "")):
        raise PermissionError("Current password is incorrect")
    validate_new_password(new_password, confirm)

    def apply(d: dict[str, Any]) -> None:
        d["password_hash"] = hash_password(new_password)
        d["session_epoch"] = int(d.get("session_epoch") or 1) + 1
    config.update("auth", apply)


def reset_auth() -> None:
    """Used by RESET_AUTH=true: forces /setup again and invalidates every existing session."""
    def apply(d: dict[str, Any]) -> None:
        d.update(setup_completed=False, password_hash="", session_epoch=int(d.get("session_epoch") or 1) + 1)
    config.update("auth", apply)
    SETUP_PASSWORD_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------- lockout

class Lockout:
    """Account lockout: MAX_FAILURES consecutive failures lock logins for LOCKOUT_SECONDS."""

    def __init__(self) -> None:
        self.failures = 0
        self.locked_until = 0.0
        self._lock = threading.Lock()

    def remaining(self) -> int:
        with self._lock:
            left = self.locked_until - time.monotonic()
            if left <= 0:
                if self.locked_until:
                    self.locked_until = 0.0
                    self.failures = 0
                return 0
            return int(left) + 1

    def fail(self) -> int:
        with self._lock:
            self.failures += 1
            if self.failures >= MAX_FAILURES:
                self.locked_until = time.monotonic() + LOCKOUT_SECONDS
                log.warning("account locked for %ss after %s failed logins", LOCKOUT_SECONDS, self.failures)
                return LOCKOUT_SECONDS
            return 0

    def success(self) -> None:
        with self._lock:
            self.failures = 0
            self.locked_until = 0.0


lockout = Lockout()


def login(username: str, password: str) -> str:
    """Return a session token. Raises PermissionError (bad creds) or TimeoutError (locked)."""
    if lockout.remaining():
        raise TimeoutError(lockout.remaining())
    data = config.load("auth")
    user_ok = hmac.compare_digest(str(data.get("username", "")), str(username or ""))
    pw_ok = verify_password(password or "", data.get("password_hash", ""))
    if not (data.get("setup_completed") and user_ok and pw_ok):
        locked_for = lockout.fail()
        if locked_for:
            raise TimeoutError(locked_for)
        raise PermissionError("Invalid username or password")
    lockout.success()
    return issue_session(data["username"], int(data.get("session_epoch") or 1))


# ---------------------------------------------------------------- sessions

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.secret_key(), salt="dm-session")


def issue_session(username: str, epoch: int) -> str:
    return _serializer().dumps({"u": username, "e": epoch})


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=session_max_age())
    except (BadSignature, SignatureExpired):
        return None
    data = config.load("auth")
    if not data.get("setup_completed"):
        return None
    if payload.get("u") != data.get("username") or payload.get("e") != int(data.get("session_epoch") or 1):
        return None
    return payload["u"]
