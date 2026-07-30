from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SECRET_KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
CLAUDE_MEM_ENV_KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")
PARENT_ENV_BLOCKLIST = (*CLAUDE_MEM_ENV_KEYS, "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL")
DEFAULT_MODE = "code--zh"
DEFAULT_PROVIDER = "claude"


class ClaudeMemConfigManager:
    def __init__(self, *, paths, claude_settings_path: Path | None = None) -> None:
        self.paths = paths
        self.claude_settings_path = claude_settings_path

    @property
    def shared_dir(self) -> Path:
        return self.paths.data_dir / "claude-mem" / "shared"

    @property
    def env_file_path(self) -> Path:
        return self.shared_dir / ".env"

    @property
    def config_file_path(self) -> Path:
        return self.shared_dir / "config.json"

    def get_config(self, *, bootstrap: bool = True) -> dict[str, Any]:
        if bootstrap:
            self.ensure_env_file()
        values = self._read_env_file()
        model = self._read_shared_config().get("model", "")
        base_url = values.get("ANTHROPIC_BASE_URL", "")
        has_secret = any(bool(values.get(key)) for key in SECRET_KEYS)
        return {
            "env_file_path": str(self.env_file_path),
            "config_file_path": str(self.config_file_path),
            "env_file_exists": self.env_file_path.exists(),
            "base_url": base_url,
            "model": model,
            "mode": DEFAULT_MODE,
            "provider": DEFAULT_PROVIDER,
            "auth_method": self._auth_method(values),
            "has_auth_token": bool(values.get("ANTHROPIC_AUTH_TOKEN")),
            "has_api_key": bool(values.get("ANTHROPIC_API_KEY")),
            "has_secret": has_secret,
        }

    def edit_snapshot(self, *, bootstrap: bool = True) -> dict[str, Any]:
        """返回仅用于并发校验的完整配置快照，不直接暴露给 API。"""
        if bootstrap:
            self.ensure_env_file()
        values = self._read_env_file()
        return {
            "base_url": values.get("ANTHROPIC_BASE_URL", ""),
            "auth_token": values.get("ANTHROPIC_AUTH_TOKEN", ""),
            "api_key": values.get("ANTHROPIC_API_KEY", ""),
            "model": self._read_shared_config().get("model", ""),
        }

    def save_config(
        self,
        *,
        base_url: str | None = None,
        auth_token: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        clear_auth_token: bool = False,
        clear_api_key: bool = False,
    ) -> dict[str, Any]:
        self.ensure_env_file()
        values = self._read_env_file()
        self._set_or_remove(values, "ANTHROPIC_BASE_URL", base_url)
        if clear_auth_token:
            values.pop("ANTHROPIC_AUTH_TOKEN", None)
        elif auth_token:
            values["ANTHROPIC_AUTH_TOKEN"] = auth_token.strip()
        if clear_api_key:
            values.pop("ANTHROPIC_API_KEY", None)
        elif api_key:
            values["ANTHROPIC_API_KEY"] = api_key.strip()
        self._write_env_file(values)
        shared_config = self._read_shared_config()
        if model is not None:
            cleaned_model = model.strip()
            if cleaned_model:
                shared_config["model"] = cleaned_model
            else:
                shared_config.pop("model", None)
        self._write_shared_config(shared_config)
        return self.get_config(bootstrap=False)

    def ensure_env_file(self) -> Path:
        if self.env_file_path.exists() and self.config_file_path.exists():
            return self.env_file_path
        values, shared_config = self._initial_values_from_claude_settings()
        if not self.env_file_path.exists():
            self._write_env_file(values)
        if not self.config_file_path.exists():
            self._write_shared_config(shared_config)
        return self.env_file_path

    def apply_to_env(self, env: dict[str, str]) -> None:
        self.ensure_env_file()
        values = self._read_env_file()
        for key in PARENT_ENV_BLOCKLIST:
            env.pop(key, None)
        env["CLAUDE_MEM_ENV_FILE"] = str(self.env_file_path)
        env["CLAUDE_MEM_PROVIDER"] = DEFAULT_PROVIDER
        env["CLAUDE_MEM_MODE"] = DEFAULT_MODE
        env["CLAUDE_MEM_CLAUDE_AUTH_METHOD"] = self._auth_method(values)
        model = self._read_shared_config().get("model", "")
        if model:
            env["CLAUDE_MEM_MODEL"] = model

    def _initial_values_from_claude_settings(self) -> tuple[dict[str, str], dict[str, str]]:
        env = self._read_claude_settings_env()
        values: dict[str, str] = {}
        for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            if env.get(key):
                values[key] = env[key]
        shared_config: dict[str, str] = {}
        model = self._normalize_claude_settings_model(
            env.get("ANTHROPIC_MODEL") or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or ""
        )
        if model:
            shared_config["model"] = model
        return values, shared_config

    def _read_claude_settings_env(self) -> dict[str, str]:
        path = self.claude_settings_path or (Path.home() / ".claude" / "settings.json")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        env = raw.get("env", {}) if isinstance(raw, dict) else {}
        if not isinstance(env, dict):
            return {}
        return {str(key): str(value) for key, value in env.items() if value is not None}

    def _read_env_file(self) -> dict[str, str]:
        if not self.env_file_path.exists():
            return {}
        values: dict[str, str] = {}
        for line in self.env_file_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in CLAUDE_MEM_ENV_KEYS:
                values[key] = value.strip().strip('"').strip("'")
        return values

    def _read_shared_config(self) -> dict[str, str]:
        if not self.config_file_path.exists():
            return {}
        try:
            raw = json.loads(self.config_file_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items() if value is not None}

    def _write_env_file(self, values: dict[str, str]) -> None:
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.shared_dir, 0o700)
        except OSError:
            pass
        ordered = [key for key in CLAUDE_MEM_ENV_KEYS if values.get(key)]
        content = "".join(f"{key}={values[key].strip()}\n" for key in ordered)
        self.env_file_path.write_text(content, encoding="utf-8")
        try:
            os.chmod(self.env_file_path, 0o600)
        except OSError:
            pass

    def _write_shared_config(self, values: dict[str, str]) -> None:
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True)
        self.config_file_path.write_text(f"{content}\n", encoding="utf-8")
        try:
            os.chmod(self.config_file_path, 0o600)
        except OSError:
            pass

    def _auth_method(self, values: dict[str, str]) -> str:
        if values.get("ANTHROPIC_BASE_URL") or values.get("ANTHROPIC_AUTH_TOKEN"):
            return "gateway"
        if values.get("ANTHROPIC_API_KEY"):
            return "api-key"
        return "cli"

    def _normalize_claude_settings_model(self, model: str) -> str:
        cleaned = model.strip()
        if cleaned.endswith("[1M]"):
            return cleaned[: -len("[1M]")].strip()
        return cleaned

    def _set_or_remove(self, values: dict[str, str], key: str, value: str | None) -> None:
        if value is None:
            return
        cleaned = value.strip()
        if cleaned:
            values[key] = cleaned
        else:
            values.pop(key, None)
