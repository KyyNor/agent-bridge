"""基于本地密码的临时管理员访问。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

import jwt

from agent_bridge.core.domain import AuthenticationRequired, ValidationError, require_admin_user
from agent_bridge.core.timeutil import utc_now


_PASSWORD_ITERATIONS = 600_000
_PASSWORD_MIN_LENGTH = 8
_PASSWORD_MAX_LENGTH = 256
_SESSION_HOURS = 12


def _encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _validate_password(password: str) -> None:
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise ValidationError(f"管理员密码至少需要 {_PASSWORD_MIN_LENGTH} 个字符")
    if len(password) > _PASSWORD_MAX_LENGTH:
        raise ValidationError(f"管理员密码不能超过 {_PASSWORD_MAX_LENGTH} 个字符")


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PASSWORD_ITERATIONS}${_encode_bytes(salt)}${_encode_bytes(digest)}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _decode_bytes(raw_salt),
            int(raw_iterations),
        )
        return hmac.compare_digest(digest, _decode_bytes(raw_digest))
    except (TypeError, ValueError):
        return False


class AdminAccessService:
    """维护全局管理员密码，并签发短期浏览器管理员会话。"""

    cookie_name = "agent_bridge_admin"

    def __init__(self, repository, admins: set[str]) -> None:
        self.repository = repository
        self.admins = admins

    def status(self, session_token: str = "") -> dict[str, Any]:
        config = self.repository.get_admin_access_config()
        subject = self.decode_session(session_token) if config and session_token else None
        return {
            "configured": config is not None,
            "active": subject is not None,
            "subject_user_id": subject,
        }

    def login(self, *, password: str, subject_user_id: str) -> dict[str, Any]:
        _validate_password(password)
        config = self.repository.get_admin_access_config()
        initialized = False
        if config is None:
            initialized = self.repository.initialize_admin_access(
                password_hash=_hash_password(password),
                session_secret=secrets.token_urlsafe(48),
                actor=subject_user_id,
            )
            config = self.repository.get_admin_access_config()
        if config is None or not _verify_password(password, str(config["password_hash"])):
            raise AuthenticationRequired("管理员密码错误")
        return {
            "configured": True,
            "active": True,
            "initialized": initialized,
            "subject_user_id": subject_user_id,
            "session_token": self._issue_session(
                subject_user_id=subject_user_id,
                secret=str(config["session_secret"]),
            ),
        }

    def change_password(
        self,
        *,
        actor: str,
        current_password: str,
        new_password: str,
    ) -> dict[str, bool]:
        require_admin_user(actor, self.admins)
        _validate_password(new_password)
        config = self.repository.get_admin_access_config()
        if config is None:
            raise ValidationError("管理员密码尚未设置，请先通过管理员切换入口完成首次设置")
        if not _verify_password(current_password, str(config["password_hash"])):
            raise AuthenticationRequired("当前管理员密码错误")
        self.repository.update_admin_access(
            password_hash=_hash_password(new_password),
            session_secret=secrets.token_urlsafe(48),
            actor=actor,
        )
        return {"updated": True}

    def decode_session(self, token: str) -> str | None:
        if not token:
            return None
        config = self.repository.get_admin_access_config()
        if config is None:
            return None
        try:
            payload = jwt.decode(
                token,
                str(config["session_secret"]),
                algorithms=["HS256"],
                options={"require": ["exp", "sub", "kind"]},
            )
        except jwt.InvalidTokenError:
            return None
        if payload.get("kind") != "agent_bridge_admin":
            return None
        subject = str(payload.get("sub") or "").strip()
        return subject or None

    @staticmethod
    def _issue_session(*, subject_user_id: str, secret: str) -> str:
        now = utc_now()
        return jwt.encode(
            {
                "kind": "agent_bridge_admin",
                "sub": subject_user_id,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(hours=_SESSION_HOURS)).timestamp()),
            },
            secret,
            algorithm="HS256",
        )
