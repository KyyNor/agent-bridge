"""DSH Web 的全局与组级配置。

配置分两层：

- 全局运行配置（``dsh_runtime_config``）：启动命令模板、空闲回收阈值，以及
  **公共模型接入**（Base URL、可用模型列表）。Base URL 留空时回落为系统
  「公共模型配置」的 Base URL。
- 组级配置（``dsh_group_configs``）：该组映射的 Linux 用户、默认模型（必须
  取自全局可用模型列表）与敏感 ``api_key``。``api_key`` 读取接口只返回
  ``api_key_set``，写入沿用 ``clear_api_key`` 语义与 ``edit_token`` 乐观并发。
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

# 默认启动命令模板；{port} 由 runtime 服务替换为动态分配的 127.0.0.1 端口，
# {patch} 在注入 Workspace 能力平面时替换为 ``--patch <配置文件>``，否则为空。
# ``--no-open`` 关闭 DSH 的浏览器自启（工作台经站内反向代理访问）。
DEFAULT_DSH_WEB_COMMAND = "dsh web {patch} --host 127.0.0.1 --port {port} --no-open"
COMMAND_PLACEHOLDERS = ("port", "patch")
# 默认空闲回收：DSH 常被当作持续工作台使用，过短的窗口会频繁触发冷启动；
# 回收严格按 last_access_at（最后一次实际访问/操作）判断，而非启动时间。
DEFAULT_IDLE_TIMEOUT_MINUTES = 720
MIN_IDLE_TIMEOUT_MINUTES = 1
MAX_IDLE_TIMEOUT_MINUTES = 30 * 24 * 60

_LINUX_USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*\$?$")


def normalize_web_command(value: str) -> str:
    """校验并归一化启动命令模板；支持 ``{port}`` 与 ``{patch}`` 占位符。"""
    command = str(value or "").strip() or DEFAULT_DSH_WEB_COMMAND
    if not command or "\n" in command or "\r" in command:
        raise ValidationError("DSH 启动命令不能为空，且不能包含换行")
    if "{" in command:
        unknown = [
            name
            for name in re.findall(r"\{([^{}]*)\}", command)
            if name not in COMMAND_PLACEHOLDERS
        ]
        if unknown:
            raise ValidationError(
                f"DSH 启动命令模板只支持 {'、'.join('{' + item + '}' for item in COMMAND_PLACEHOLDERS)} 占位符，"
                f"检测到：{'、'.join(sorted(set(unknown)))}"
            )
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


def normalize_base_url(value: str) -> str:
    cleaned = str(value or "").strip().rstrip("/")
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("Base URL 必须是 http 或 https 的完整地址")
    return cleaned


def normalize_models(values: list[str] | None) -> list[str]:
    models: list[str] = []
    for raw in values or []:
        name = str(raw).strip()
        if name and name not in models:
            models.append(name)
    return models


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
        """runtime 启动注入用读取入口：返回含 api_key 原值的完整组配置。"""
        return self._repo.get_group_config(group_key)

    def save_group_config(
        self,
        actor: str,
        *,
        group_key: str,
        linux_user: str = "",
        default_model: str = "",
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

        cleaned_default = default_model.strip()
        available_models = self._resolve_runtime_config()["available_models"]
        if cleaned_default and cleaned_default not in available_models:
            raise ValidationError(
                f"默认模型 {cleaned_default!r} 不在全局可用模型列表中，请先在运行配置里添加该模型"
            )
        if clear_api_key and api_key and api_key.strip():
            raise ValidationError("不能同时设置和清除 API Key")

        saved = self._repo.save_group_config(
            group_key=normalized_group,
            linux_user=resolved_linux_user,
            default_model=cleaned_default,
            api_key=api_key.strip() if api_key else None,
            clear_api_key=clear_api_key,
            updated_by=actor,
        )
        logger.info(
            "DSH 组配置已保存 group=%s linux_user=%s default_model=%s api_key_set=%s actor=%s",
            normalized_group,
            resolved_linux_user,
            cleaned_default,
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
        """runtime 服务读取入口：补齐默认值与 Base URL 回落，不附带 edit token。"""
        return self._resolve_runtime_config()

    def save_runtime_config(
        self,
        actor: str,
        *,
        web_command: str,
        idle_timeout_minutes: int,
        base_url: str = "",
        available_models: list[str] | None = None,
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
            base_url=normalize_base_url(base_url),
            available_models=normalize_models(available_models),
        )
        resolved = self._resolve_runtime_config_from(saved)
        logger.info(
            "DSH 运行配置已保存 web_command=%s idle_timeout_minutes=%s models=%d actor=%s",
            cleaned_command,
            cleaned_timeout,
            len(resolved["available_models"]),
            actor,
        )
        return attach_edit_token(
            self._public_runtime_config(resolved),
            self._runtime_config_snapshot(resolved),
        )

    def model_binding_for(self, group_key: str) -> dict[str, Any]:
        """汇总注入 DSH 进程的模型接入信息（公共接入 + 组级默认模型与密钥）。"""
        runtime = self._resolve_runtime_config()
        group = self._repo.get_group_config(group_key) or {}
        return {
            "base_url": runtime["base_url"],
            "base_url_source": runtime["base_url_source"],
            "available_models": runtime["available_models"],
            "default_model": str(group.get("default_model") or ""),
            "api_key": str(group.get("api_key") or ""),
        }

    # -- 内部工具 --

    @property
    def _repo(self):
        return self._store.dsh_config

    def _public_config_base_url(self) -> str:
        """「公共模型配置」的 Base URL，作为全局 Base URL 留空时的回落值。"""
        config = self._store.get_retrieval_probe_llm_config()
        return str(config.get("base_url") or "").strip().rstrip("/")

    def _resolve_runtime_config(self) -> dict[str, Any]:
        return self._resolve_runtime_config_from(self._repo.get_runtime_config())

    def _resolve_runtime_config_from(self, raw: dict[str, Any]) -> dict[str, Any]:
        command = str(raw.get("web_command") or "").strip() or DEFAULT_DSH_WEB_COMMAND
        try:
            timeout = int(raw.get("idle_timeout_minutes") or 0)
        except (TypeError, ValueError):
            timeout = 0
        if timeout <= 0:
            timeout = DEFAULT_IDLE_TIMEOUT_MINUTES
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        resolved_base_url = base_url
        source = "global" if base_url else ""
        if not base_url:
            resolved_base_url = self._public_config_base_url()
            source = "public_model_config" if resolved_base_url else ""
        return {
            "web_command": command,
            "idle_timeout_minutes": timeout,
            "saved_base_url": base_url,
            "base_url": resolved_base_url,
            "base_url_source": source,
            "available_models": normalize_models(raw.get("available_models") or []),
            "updated_at": raw.get("updated_at"),
        }

    @staticmethod
    def _public_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "web_command": str(config.get("web_command") or ""),
            "idle_timeout_minutes": int(config.get("idle_timeout_minutes") or 0),
            "base_url": str(config.get("saved_base_url") or ""),
            "resolved_base_url": str(config.get("base_url") or ""),
            "base_url_source": str(config.get("base_url_source") or ""),
            "available_models": list(config.get("available_models") or []),
            "updated_at": config.get("updated_at"),
        }

    @staticmethod
    def _runtime_config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
        """并发快照只含全局配置自身字段；Base URL 回落值不参与，避免公共模型配置变更误报冲突。"""
        return {
            "web_command": str(config.get("web_command") or ""),
            "idle_timeout_minutes": int(config.get("idle_timeout_minutes") or 0),
            "base_url": str(config.get("saved_base_url") or ""),
            "available_models": list(config.get("available_models") or []),
        }

    @classmethod
    def _public_group_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        public = {
            "group_key": str(config.get("group_key") or ""),
            "linux_user": str(config.get("linux_user") or ""),
            "default_model": str(config.get("default_model") or ""),
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
            "default_model": str(config.get("default_model") or ""),
            # 快照必须包含秘密原值，以便发现其他页面对秘密的修改。
            "api_key": str(config.get("api_key") or ""),
        }
