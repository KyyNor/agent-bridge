"""记忆后端配置：平台私有文件、秘密脱敏与乐观并发保护。"""
from __future__ import annotations

import os
import json
import threading
from urllib.parse import urlsplit

from agent_bridge.agent_workspace import safe_files
from agent_bridge.core.domain import ValidationError
from agent_bridge.core.editing import attach_edit_token, require_edit_token


class MemoryBackendConfig:
    def __init__(self, paths):
        self.path = paths.data_dir / "memory" / "backend.json"
        self._lock = threading.RLock()

    def read(self):
        if not self.path.exists() and not self.path.is_symlink():
            return {"backend": "claude-mem", "url": "", "api_token": ""}
        text = safe_files.read_text_secure(self.path, label="记忆后端配置")
        try:
            data = json.loads(text) if text is not None else None
        except ValueError as exc:
            raise ValidationError("记忆后端配置损坏，请修复配置；不会自动切换后端") from exc
        if not isinstance(data, dict) or data.get("backend") not in {"claude-mem", "hindsight"}:
            raise ValidationError("记忆后端配置无效；不会自动切换后端")
        return {"url": "", "api_token": "", **data}

    def public(self):
        data = self.read()
        return attach_edit_token({"backend": data["backend"], "url": data["url"],
                                  "api_token_set": bool(data["api_token"])}, data)

    def save(self, *, actor, backend, url, api_token=None, clear_api_token=False, expected_edit_token=None):
        with self._lock:
            data = self.read()
            require_edit_token(expected_edit_token, data, resource_type="记忆后端配置", resource_key="global", actor=actor)
            if backend not in {"claude-mem", "hindsight"}:
                raise ValidationError("未知记忆后端")
            url = url.strip().rstrip("/")
            parsed = urlsplit(url)
            if url and (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                        parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValidationError("Hindsight URL 必须为不含凭据、查询串或片段的 HTTP 地址")
            if backend == "hindsight" and not url:
                raise ValidationError("请填写 Hindsight URL")
            token = "" if clear_api_token else (api_token.strip() if api_token else data["api_token"])
            if self.path.is_symlink():
                raise ValidationError("记忆后端配置不能是软链")
            safe_files.write_managed_json_atomic(self.path, {"backend": backend, "url": url, "api_token": token},
                                                uid=os.geteuid(), gid=os.getegid(), label="记忆后端配置")
            return self.public()
