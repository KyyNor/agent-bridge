from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Literal

import jwt
from fastapi import Request

from agent_bridge.core.domain import AuthenticationRequired, ValidationError


IdentitySource = Literal["web_sso", "linux_cli", "password_admin", "system"]


@dataclass(frozen=True)
class ActorIdentity:
    """一次请求中已经确认的调用身份。"""

    user_id: str
    user_name: str
    source: IdentitySource


@dataclass(frozen=True)
class IdentityConfig:
    """内部身份接入配置；未配置 SSO 时保留可信 CLI Header 兼容入口。"""

    sso_secret: str = ""
    sso_algorithm: str = "HS256"
    user_id_claim: str = "user_id"
    user_name_claim: str = "user_name"
    cookie_name: str = "agent_bridge_sso"
    cookie_secure: bool = False
    allow_cli_header: bool = True


class RequestIdentityResolver:
    """把 Web SSO Cookie 或内部 CLI Header 统一解析为调用身份。"""

    cli_header = "X-Agent-Bridge-User"

    def __init__(
        self,
        config: IdentityConfig,
        *,
        admin_access=None,
        admin_actor_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self.config = config
        self.admin_access = admin_access
        self.admin_actor_provider = admin_actor_provider

    def resolve(self, request: Request) -> ActorIdentity:
        if self.admin_access is not None:
            admin_token = request.cookies.get(self.admin_access.cookie_name, "").strip()
            subject = self.admin_access.decode_session(admin_token)
            if subject:
                admin_actor = self.admin_actor_provider() if self.admin_actor_provider else None
                if not admin_actor:
                    raise AuthenticationRequired("Agent Bridge 尚未配置维护管理员")
                return ActorIdentity(
                    user_id=admin_actor,
                    user_name=f"{subject}（管理员）",
                    source="password_admin",
                )
        return self.resolve_base(request)

    def resolve_base(self, request: Request) -> ActorIdentity:
        token = request.cookies.get(self.config.cookie_name, "").strip()
        if token:
            return self.decode_sso_token(token)

        if self.config.allow_cli_header:
            linux_user = request.headers.get(self.cli_header, "").strip()
            if linux_user:
                return ActorIdentity(user_id=linux_user, user_name=linux_user, source="linux_cli")

        raise AuthenticationRequired("未识别到 Agent Bridge 调用者")

    def decode_sso_token(self, token: str) -> ActorIdentity:
        if not self.config.sso_secret:
            raise AuthenticationRequired("Agent Bridge 尚未配置统一登录密钥")
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self.config.sso_secret,
                algorithms=[self.config.sso_algorithm],
                options={"require": ["exp", self.config.user_id_claim]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationRequired("统一登录凭证已过期") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationRequired("统一登录凭证无效") from exc

        user_id = str(payload.get(self.config.user_id_claim) or "").strip()
        if not user_id:
            raise ValidationError("统一登录凭证缺少用户 ID")
        user_name = str(payload.get(self.config.user_name_claim) or user_id).strip() or user_id
        return ActorIdentity(user_id=user_id, user_name=user_name, source="web_sso")
