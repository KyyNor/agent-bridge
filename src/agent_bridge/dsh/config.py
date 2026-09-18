"""DSH Web 的组级与全局运行配置。

组级配置（base_url / 模型 / API Key）按业务 group 维护一份，启动 DSH Web
时由 runtime 服务读取并注入进程环境；``api_key`` 是敏感配置，读取接口只
返回 ``api_key_set``，写入沿用 ``clear_api_key`` 语义与 ``edit_token``
乐观并发协议。
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from agent_bridge.core.domain import ValidationError, require_admin_user
from agent_bridge.core.editing import attach_edit_token, require_edit_token
from agent_bridge.core.timeutil import utc_iso

logger = logging.getLogger(__name__)

# 默认启动命令模板；{port} 由 runtime 服务替换为动态分配的 127.0.0.1 端口。
# DSH CLI 参数形态以实际版本为准，可在系统管理页调整。
DEFAULT_DSH_WEB_COMMAND = "dsh web --host 127.0.0.1 --port {port}"
DEFAULT_IDLE_TIMEOUT_MINUTES = 120
MIN_IDLE_TIMEOUT_MINUTES = 1
MAX_IDLE_TIMEOUT_MINUTES = 30 * 24 * 60

_LINUX_USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*\$?$")


def normalize_web_command(value: str) -> str:
    """校验并归一化启动命令模板；必须包含可执行文件且允许 ``{port}`` 占位。"""
    command = str(value or "").strip() or DEFAULT_DSH_WEB_COMMAND
    if not command or "\n" in command or "\r" in command:
        raise ValidationError("DSH 启动命令不能为空，且不能包含换行")
    return command


def normalize_idle_timeout_minutes(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("空闲回收时长必须是整数分钟") from exc
    if not MIN_IDLE_TIMEOUT_MINUTES <= timeout <= MAX_IDLE_TIMEOUT_MINUTES:
        raise ValidationError(
            f"空闲回收时长必须在 {MIN_IDLE_TIMEOUT_MINUTES}-{MAX_IDLE_TIMEOUT_MINUTES} 分钟之间"
        )
    return timeout


class DshConfigService:
    """DSH 配置的校验、脱敏与并发保护入口。"""

    def __init__(self, *, store, admins: set[str], access) -> None:
        self._store = store
        self.admins = admins
        self._access = access

    # -- 组级配置 --

    def list_group_configs(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return [self._public_group_config(config) for config in self._repo.list_group_configs()]

    def get_group_config(self, actor: str, group_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        config = self._repo.get_group_config(group_key)
        if config is None:
            raise ValidationError(f"小组 {group_key} 尚未配置 DSH")
        return self._public_group_config(config)

    def group_config_for_runtime(self, group_key: str) -> dict[str, Any] | None:
        """runtime 启动注入用读取入口：返回含 api_key 原值的完整配置。"""
        return self._repo.get_group_config(group_key)

    def save_group_config(
        self,
        actor: str,
        *,
        group_key: str,
        linux_user: str = "",
        base_url: str = "",
        default_model: str = "",
        available_models: list[str] | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_group = group_key.strip().lower()
        if not normalized_group:
            raise ValidationError("小组标识不能为空")
        if self._access.repository.get_group(normalized_group) is None:
            raise ValidationError(f"小组 {normalized_group} 不存在，请先在小组权限页创建")

        current = self._repo.get_group_config(normalized_group)
        require_edit_token(
            expected_edit_token,
            self._group_config_snapshot(current),
            resource_type="DSH 组配置",
            resource_key=normalized_group,
            actor=actor,
        )

        resolved_linux_user = linux_user.strip() or normalized_group
        if not _LINUX_USER_PATTERN.fullmatch(resolved_linux_user):
            raise ValidationError(f"Linux 用户名不合法：{resolved_linux_user!r}")

        cleaned_url = base_url.strip().rstrip("/")
        if cleaned_url:
            parsed = urlparse(cleaned_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValidationError("base_url 必须是 http 或 https 的完整地址")

        models: list[str] = []
        for raw in available_models or []:
            name = str(raw).strip()
            if name and name not in models:
                models.append(name)
        cleaned_default = default_model.strip()
        if cleaned_default and cleaned_default not in models:
            raise ValidationError("default_model 必须包含在 available_models 列表中")
        if clear_api_key and api_key and api_key.strip():
            raise ValidationError("不能同时设置和清除 API Key")

        saved = self._repo.save_group_config(
            group_key=normalized_group,
            linux_user=resolved_linux_user,
            base_url=cleaned_url,
            default_model=cleaned_default,
            available_models=models,
            api_key=api_key.strip() if api_key else None,
            clear_api_key=clear_api_key,
            updated_by=actor,
        )
        logger.info(
            "DSH 组配置已保存 group=%s linux_user=%s base_url=%s models=%d api_key_set=%s actor=%s",
            normalized_group,
            resolved_linux_user,
            cleaned_url,
            len(models),
            bool(saved.get("api_key")),
            actor,
        )
        return self._public_group_config(saved)

    # -- 全局运行配置 --

    def get_runtime_config(self, actor: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        resolved = self._resolve_runtime_config()
        return attach_edit_token(
            self._public_runtime_config(resolved),
            self._runtime_config_snapshot(resolved),
        )

    def runtime_config_for_runtime(self) -> dict[str, Any]:
        """runtime 服务读取入口：补齐默认值，不附带 edit token。"""
        return self._resolve_runtime_config()

    def save_runtime_config(
        self,
        actor: str,
        *,
        web_command: str,
        idle_timeout_minutes: int,
        expected_edit_token: str | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        current = self._resolve_runtime_config()
        require_edit_token(
            expected_edit_token,
            self._runtime_config_snapshot(current),
            resource_type="DSH 运行配置",
            resource_key="global",
            actor=actor,
        )
        cleaned_command = normalize_web_command(web_command)
        cleaned_timeout = normalize_idle_timeout_minutes(idle_timeout_minutes)
        saved = self._repo.save_runtime_config(
            web_command=cleaned_command,
            idle_timeout_minutes=cleaned_timeout,
        )
        resolved = self._resolve_runtime_config_from(saved)
        logger.info(
            "DSH 运行配置已保存 web_command=%s idle_timeout_minutes=%s actor=%s",
            cleaned_command,
            cleaned_timeout,
            actor,
        )
        return attach_edit_token(
            self._public_runtime_config(resolved),
            self._runtime_config_snapshot(resolved),
        )

    # -- 内部工具 --

    @property
    def _repo(self):
        return self._store.dsh_config

    def _resolve_runtime_config(self) -> dict[str, Any]:
        return self._resolve_runtime_config_from(self._repo.get_runtime_config())

    @staticmethod
    def _resolve_runtime_config_from(raw: dict[str, Any]) -> dict[str, Any]:
        command = str(raw.get("web_command") or "").strip() or DEFAULT_DSH_WEB_COMMAND
        try:
            timeout = int(raw.get("idle_timeout_minutes") or 0)
        except (TypeError, ValueError):
            timeout = 0
        if timeout <= 0:
            timeout = DEFAULT_IDLE_TIMEOUT_MINUTES
        return {
            "web_command": command,
            "idle_timeout_minutes": timeout,
            "updated_at": raw.get("updated_at"),
        }

    @staticmethod
    def _public_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "web_command": str(config.get("web_command") or ""),
            "idle_timeout_minutes": int(config.get("idle_timeout_minutes") or 0),
            "updated_at": config.get("updated_at"),
        }

    @staticmethod
    def _runtime_config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "web_command": str(config.get("web_command") or ""),
            "idle_timeout_minutes": int(config.get("idle_timeout_minutes") or 0),
        }

    @classmethod
    def _public_group_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        public = {
            "group_key": str(config.get("group_key") or ""),
            "linux_user": str(config.get("linux_user") or ""),
            "base_url": str(config.get("base_url") or ""),
            "default_model": str(config.get("default_model") or ""),
            "available_models": list(config.get("available_models") or []),
            "api_key_set": bool(config.get("api_key")),
            "updated_by": str(config.get("updated_by") or ""),
            "updated_at": config.get("updated_at") or utc_iso(),
        }
        return attach_edit_token(public, cls._group_config_snapshot(config))

    @staticmethod
    def _group_config_snapshot(config: dict[str, Any] | None) -> dict[str, Any] | None:
        if config is None:
            return None
        return {
            "group_key": str(config.get("group_key") or ""),
            "linux_user": str(config.get("linux_user") or ""),
            "base_url": str(config.get("base_url") or ""),
            "default_model": str(config.get("default_model") or ""),
            "available_models": list(config.get("available_models") or []),
            # 快照必须包含秘密原值，以便发现其他页面对秘密的修改。
            "api_key": str(config.get("api_key") or ""),
        }
