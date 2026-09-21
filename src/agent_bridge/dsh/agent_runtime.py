"""AgentRuntime 的 DSH 执行绑定解析。

Agent Bridge 的 Coding Agent ``dsh`` 后端按 run 启动独立、短生命周期的
``dsh --profile acp`` 进程（ACP JSON-RPC over stdio）。本模块在 run 开始时
把当前业务用户所属 group 的 DSH 配置（模型接入、API Key、Linux 身份）解析成
一份执行绑定，供 adapter 构造进程环境与模型路由 patch。

模型接入复用 DSH Web Runtime 的同一套配置与用户级目录
（``<linux home>/.config/dsh/<business-user>/``）：settings.yaml 中的托管
供应商条目由本模块幂等注入，后台 run 因此不依赖 Web Runtime 是否已启动。
"""

from __future__ import annotations

import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_bridge.core.domain import ValidationError
from agent_bridge.dsh import injection
from agent_bridge.dsh.launcher import LinuxIdentity, resolve_linux_identity

logger = logging.getLogger(__name__)

# DSH 期望 HOME 已完成一次基础初始化：这些标准子目录缺失时 acp profile
# 的部分插件初始化失败，settings.yaml 的托管供应商不会注册（prompt 报
# ``no adapter registered for provider``）。预创建幂等，不与 Web Runtime 冲突。
_DSH_HOME_DIRECTORIES = ("sessions", "storages", "change-ledger", "task-board")


@dataclass(frozen=True)
class DshAgentExecutionBinding:
    """一次 DSH agent run 的启动参数。"""

    command: str
    dsh_home: Path
    provider: str
    model: str
    identity: LinuxIdentity
    api_key_env: dict[str, str]
    base_url: str

    def process_env(self) -> dict[str, str]:
        """构造 ACP 进程的环境变量覆盖（不含 os.environ 基底）。"""
        env = {
            "HOME": str(self.identity.home),
            "USER": self.identity.user,
            "LOGNAME": self.identity.user,
            "DSH_HOME": str(self.dsh_home),
        }
        env.update(self.api_key_env)
        return env


@runtime_checkable
class DshAgentRuntimeDependency(Protocol):
    """Coding Agent adapter 依赖的执行绑定解析入口。"""

    def resolve_execution(
        self, actor: str | None, owner_group_key: str | None, *, model: str | None = None
    ) -> DshAgentExecutionBinding: ...


class DshAgentRuntimeResolver:
    """按业务用户解析 group 级 DSH 配置并准备用户级配置目录。"""

    def __init__(self, *, configs: Any, access: Any, passwd_lookup: Any = None) -> None:
        self._configs = configs
        self._access = access
        self._passwd_lookup = passwd_lookup

    def resolve_execution(
        self, actor: str | None, owner_group_key: str | None, *, model: str | None = None
    ) -> DshAgentExecutionBinding:
        if not actor:
            raise ValidationError("DSH 后端需要业务用户上下文（actor）才能解析组级配置")
        group_key = owner_group_key or self._access.actor_group_key(actor, required=True)
        config = self._configs.group_config_for_runtime(group_key)
        if config is None:
            raise ValidationError(
                f"小组 {group_key} 尚未配置 DSH，请先在系统管理页完成组级配置"
            )

        binding = self._configs.model_binding_for(group_key)
        base_url = str(binding.get("base_url") or "")
        available_models = [str(item) for item in binding.get("available_models") or []]
        if not base_url or not available_models:
            raise ValidationError(
                f"小组 {group_key} 的 DSH 模型接入未就绪（Base URL 或可用模型列表为空），"
                "请先在系统管理页的 DSH 运行配置中补全"
            )
        resolved_model = self._resolve_model(
            model=model,
            group_default=str(binding.get("default_model") or ""),
            available=available_models,
        )
        if resolved_model not in available_models:
            raise ValidationError(
                f"DSH 后端配置的模型 {resolved_model!r} 不在可用模型列表中，"
                f"可选值：{'、'.join(available_models)}"
            )

        linux_user = str(config.get("linux_user") or group_key)
        identity = resolve_linux_identity(linux_user, self._passwd_lookup)
        dsh_home = self._ensure_config_dir(identity, actor)
        self._inject_settings(dsh_home, identity, binding, resolved_model)

        command = dsh_binary_from_command(
            str(self._configs.runtime_config_for_runtime().get("web_command") or "")
        )
        resolved = DshAgentExecutionBinding(
            command=command,
            dsh_home=dsh_home,
            provider=injection.MANAGED_PROVIDER_KEY,
            model=resolved_model,
            identity=identity,
            api_key_env=injection.managed_api_key_env_value(str(binding.get("api_key") or "")),
            base_url=base_url,
        )
        logger.info(
            "DSH agent 执行绑定已解析 actor=%s group=%s linux_user=%s model=%s dsh_home=%s",
            actor,
            group_key,
            linux_user,
            resolved.model,
            dsh_home,
        )
        return resolved

    @staticmethod
    def _resolve_model(*, model: str | None, group_default: str, available: list[str]) -> str:
        for candidate in (model, group_default):
            cleaned = str(candidate or "").strip()
            if cleaned:
                return cleaned
        return available[0]

    def _ensure_config_dir(self, identity: LinuxIdentity, user_id: str) -> Path:
        """创建 ``<linux home>/.config/dsh/<business-user>/`` 并归属目标用户。

        只对本次新建的目录段（含标准子目录）执行 chown；已存在的目录保持
        原样。root 降权场景下新建目录默认归属 root，若不归属目标用户，
        降权后的 DSH 进程无法写入（sessions/storages 等均为其工作目录）。
        """
        dsh_root = identity.home / ".config" / "dsh"
        user_dir = dsh_root / user_id
        created: list[Path] = []
        probe = dsh_root
        while not probe.exists():
            created.append(probe)
            parent = probe.parent
            if parent == probe:
                break
            probe = parent
        user_dir.mkdir(parents=True, exist_ok=True)
        created_subdirs: list[Path] = []
        for name in _DSH_HOME_DIRECTORIES:
            subdir = user_dir / name
            if not subdir.exists():
                created_subdirs.append(subdir)
            subdir.mkdir(parents=True, exist_ok=True)
        running_uid = os.geteuid()
        if running_uid == 0 and identity.uid != running_uid:
            for path in [*created, user_dir, *created_subdirs]:
                try:
                    os.chown(path, identity.uid, identity.gid)
                except OSError as exc:
                    logger.warning(
                        "DSH 配置目录 chown 失败 path=%s uid=%s 原因=%s",
                        path,
                        identity.uid,
                        exc,
                    )
        try:
            user_dir.chmod(0o700)
        except OSError as exc:
            logger.warning("DSH 配置目录 chmod 失败 path=%s 原因=%s", user_dir, exc)
        return user_dir

    @staticmethod
    def _inject_settings(
        dsh_home: Path, identity: LinuxIdentity, binding: dict[str, Any], model: str
    ) -> None:
        """把托管供应商与默认模型幂等写入 settings.yaml（与 Web Runtime 同源）。"""
        injection.write_settings(
            dsh_home,
            base_url=str(binding.get("base_url") or ""),
            models=[str(item) for item in binding.get("available_models") or []],
            default_model=model,
        )
        settings_file = injection.settings_path(dsh_home)
        if settings_file.exists():
            injection.ensure_owner(settings_file, uid=identity.uid, gid=identity.gid)


def dsh_binary_from_command(web_command: str) -> str:
    """从启动命令模板推导 dsh 可执行名（ACP 进程与 web 进程同一通道）。"""
    try:
        argv = shlex.split(web_command)
    except ValueError:
        argv = []
    return argv[0] if argv else "dsh"
