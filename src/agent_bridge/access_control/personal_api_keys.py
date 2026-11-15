"""个人知识库 API 凭据：数据库只存随机 token 的摘要。"""
from __future__ import annotations

import hashlib
import logging
import secrets

from agent_bridge.core.domain import AuthenticationRequired
from agent_bridge.core.timeutil import utc_iso

logger = logging.getLogger(__name__)


class PersonalApiKeys:
    def __init__(self, repository):
        self.repository = repository

    def status(self, actor):
        self.repository.ensure_user(user_id=actor, actor=actor)
        if self.repository.get_user(actor) is None:
            raise AuthenticationRequired("当前用户已停用")
        row = self.repository.get_personal_api_key(actor)
        return {"has_key": row is not None, "masked_key": row["prefix"] + "…" if row else "", "updated_at": row["updated_at"] if row else None}

    def reset(self, actor):
        self.status(actor)
        token = "agb_kb_" + secrets.token_urlsafe(32)
        self.repository.replace_personal_api_key(actor, self._hash(token), token[:14], utc_iso())
        logger.info("知识库个人 API Key 已重置 user=%s 状态=成功", actor)
        return {**self.status(actor), "api_key": token}

    def authenticate(self, token):
        if not token or not token.startswith("agb_kb_") or len(token) > 128:
            raise AuthenticationRequired("API Key 无效或已重置")
        actor = self.repository.personal_api_key_owner(self._hash(token))
        if actor is None:
            raise AuthenticationRequired("API Key 无效或已重置")
        return actor

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()
