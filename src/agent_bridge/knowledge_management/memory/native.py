"""工作台原生记忆插件：隔离安装、启动快照与 bank 授权。

只加载官方原生插件，不添加第二个 MCP server。capability 与上游密钥均不落盘。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import weakref
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

from agent_bridge.agent_workspace import safe_files
from agent_bridge.agent_workspace.launcher import PopenDshLauncher
from agent_bridge.core.domain import AccessDenied, ValidationError

logger = logging.getLogger(__name__)

PACKAGE_NAME = "@vectorize-io/hindsight-coding-agents"
PACKAGE_VERSION = "0.8.7"
PACKAGE_SPEC = PACKAGE_NAME + "@" + PACKAGE_VERSION
NATIVE_MEMORY_ACTIVE = ContextVar("native_memory_active", default=False)
CONFIG_NAME = "agent-bridge-hindsight.json"
GATEWAY_PREFIX = "/api/v1/memory/hindsight"


@dataclass(frozen=True)
class NativeMemoryGrant:
    block_key: str
    bank_id: str
    settings: dict
    owner_group_key: str


def isolated_environment(env: dict[str, str]) -> dict[str, str]:
    """清除继承的全局插件路由/凭据，避免环境与用户配置覆盖托管选择。"""
    return {key: value for key, value in env.items() if not key.startswith("HINDSIGHT_")}


def plugin_config(*, enabled: bool, bank_id: str = "", api_url: str = "") -> dict:
    return {
        "disabled": not enabled, "serverMode": "self-hosted", "apiUrl": api_url, "bankId": bank_id, "dynamicBankId": False,
        "autoUpdate": False, "autoSeed": False, "codebaseSurvey": False, "gitIngest": "none",
        "manageBankConfig": False, "autoInject": "recall", "retainSessions": enabled,
        "pageTriggerType": "manual",
    }


def allowed_gateway_path(method: str, path: str, bank_id: str) -> bool:
    """显式白名单；禁止编码路径、遍历、bank 管理与跨 bank 路由。"""
    if "%" in path or "\\" in path or any(part in {".", ".."} for part in path.split("/")):
        return False
    if path in {"/v1/version", "/version"}:
        return method == "GET"
    prefix = "/v1/default/banks/" + bank_id
    if not path.startswith(prefix + "/"):
        return False
    suffix = path[len(prefix):]
    reads = r"/(?:config|documents(?:/[^/]+)?|operations(?:/[^/]+)?|pages/(?:tree|search|[^/]+)|knowledge-base/(?:tree|pages/[^/]+))"
    writes = {"/memories", "/memories/recall", "/reflect", "/pages", "/knowledge-base/pages"}
    return ((method == "GET" and re.fullmatch(reads, suffix) is not None) or
            (method == "POST" and suffix in writes) or
            (method == "PATCH" and re.fullmatch(r"/pages/nodes/[^/]+", suffix) is not None))


class NativeMemoryIntegration:
    def __init__(self, memory):
        self.memory = memory
        self._grants = weakref.WeakKeyDictionary()
        self._lock = threading.RLock()

    def _resolve(self, workspace):
        settings = self.memory.backend_config.read()
        if workspace is not None and workspace.capability is not None:
            with self._lock:
                self._grants.pop(workspace.capability, None)
        if settings["backend"] != "hindsight" or workspace is None or workspace.capability is None:
            return None
        capability = workspace.capability
        if self.memory.access is None:
            raise AccessDenied("原生记忆需要归属组权限服务")
        with self.memory.access.bind_actor_group(actor=capability.user_id, owner_group_key=capability.owner_group_key):
            resolved = self.memory.resolve_profile_block(capability.user_id, capability.profile_key)
        block = resolved.get("block")
        if resolved["status"] != "ok" or block is None:
            return None
        profile = self.memory.store.get_project_profile(capability.profile_key)
        if (profile is None or profile.get("owner_group_key") != capability.owner_group_key or
                block.get("owner_group_key") != capability.owner_group_key):
            raise AccessDenied("记忆区块归属组与工作台不匹配")
        backend = self.memory.backends.require("hindsight")
        backend.ensure_bank(block, settings)
        grant = NativeMemoryGrant(block["block_key"], backend.bank_id(block), dict(settings), capability.owner_group_key)
        with self._lock:
            self._grants[capability] = grant
        return grant

    @contextmanager
    def mcp_scope(self, capability):
        with self._lock:
            active = capability in self._grants
        token = NATIVE_MEMORY_ACTIVE.set(active)
        try:
            yield
        finally:
            NATIVE_MEMORY_ACTIVE.reset(token)

    def require_grant(self, capability):
        # 个人主体重新校验当前组；共享主体校验组到 Linux 身份的现行映射。
        if capability.shared_runtime:
            group_config = self.memory.store.dsh_config.get_group_config(capability.owner_group_key) or {}
            if group_config.get("linux_user") != capability.user_id:
                raise AccessDenied("共享工作台的 Linux 身份映射已变化")
        elif self.memory.access.actor_group_key(capability.user_id) != capability.owner_group_key:
            raise AccessDenied("工作台用户归属组已变化")
        with self._lock:
            grant = self._grants.get(capability)
        if grant is None:
            raise AccessDenied("当前工作台未启用 Hindsight 记忆")
        with self.memory.access.bind_actor_group(actor=capability.user_id, owner_group_key=capability.owner_group_key):
            resolved = self.memory.resolve_profile_block(capability.user_id, capability.profile_key)
        block = resolved.get("block")
        profile = self.memory.store.get_project_profile(capability.profile_key)
        if (profile is None or profile.get("owner_group_key") != grant.owner_group_key or resolved["status"] != "ok" or block is None or block["block_key"] != grant.block_key or
                block.get("owner_group_key") != grant.owner_group_key or
                capability.owner_group_key != grant.owner_group_key or
                self.memory.backends.require("hindsight").bank_id(block) != grant.bank_id):
            raise AccessDenied("工作台记忆绑定或归属已变化，请重新启动实例")
        return grant

    def prepare(self, *, config_dir, identity, workspace, env, mcp_url, harness, launcher=None, dsh_binary=None):
        """每次启动读取配置；正在运行的共享实例不调用此入口。"""
        env = isolated_environment(env)
        grant = self._resolve(workspace)
        gateway = urlsplit(mcp_url)
        api_url = f"{gateway.scheme}://{gateway.netloc}{GATEWAY_PREFIX}"
        config_path = config_dir / CONFIG_NAME
        if config_path.is_symlink():
            raise ValidationError("Hindsight 托管配置不能是软链")
        safe_files.write_managed_json_atomic(
            config_path, plugin_config(enabled=grant is not None, bank_id=grant.bank_id if grant else "", api_url=api_url),
            uid=identity.uid, gid=identity.gid, label="Hindsight 原生插件配置",
        )
        env["HINDSIGHT_CONFIG"] = str(config_path)
        for variable, filename in (("HINDSIGHT_LOG_FILE", "hindsight-plugin.log"),
                                   ("HINDSIGHT_DIAG_FILE", "hindsight-diag.jsonl"),
                                   ("HINDSIGHT_USAGE_FILE", "hindsight-usage.jsonl")):
            env[variable] = str(config_dir / filename)
        if grant is not None:
            env["HINDSIGHT_API_TOKEN"] = workspace.capability.token
        return NATIVE_ADAPTERS[harness].apply(self, config_dir, identity, env, grant, launcher)

    def enabled_for(self, workspace):
        if workspace is None or workspace.capability is None or self.memory.backend_config.read()["backend"] != "hindsight":
            return False
        capability = workspace.capability
        with self.memory.access.bind_actor_group(actor=capability.user_id, owner_group_key=capability.owner_group_key):
            return self.memory.resolve_profile_block(capability.user_id, capability.profile_key)["status"] == "ok"

    @staticmethod
    def _pi_entry(config_dir):
        return config_dir / "node_modules" / PACKAGE_NAME / "dist" / "pi.js"

    def _install_pi(self, config_dir, identity, env, launcher):
        entry = self._pi_entry(config_dir)
        package_json = entry.parent.parent / "package.json"
        if safe_files.read_managed_json(package_json).get("version") == PACKAGE_VERSION and entry.is_file():
            return entry
        npm = shutil.which("npm")
        if not npm:
            raise ValidationError("Hindsight 原生插件需要 npm，请先安装 Node.js")
        # 安装不携带 capability 与模型密钥，也不执行依赖生命周期脚本。
        install_env = {key: value for key, value in isolated_environment(env).items()
                       if not any(secret in key.upper() for secret in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CAPABILITY"))}
        code, _ = (launcher or PopenDshLauncher()).run_once(
            command=[npm, "install", "--prefix", str(config_dir), "--no-audit", "--no-fund", "--ignore-scripts",
                     "--no-package-lock", "--save-exact", PACKAGE_SPEC],
            env=install_env, cwd=config_dir, identity=identity, timeout_seconds=240,
        )
        if code or safe_files.read_managed_json(package_json).get("version") != PACKAGE_VERSION or not entry.is_file():
            logger.warning("Hindsight 原生插件安装失败 阶段=安装 状态码=%s", code)
            raise ValidationError("Hindsight 原生插件安装失败，请检查网络与 npm 依赖")
        return entry


class PiNativeMemoryAdapter:
    @staticmethod
    def apply(integration, config_dir, identity, env, grant, launcher):
        entry = integration._install_pi(config_dir, identity, env, launcher) if grant else None
        path = config_dir / "settings.json"
        settings = safe_files.read_managed_json(path)
        extensions = [item for item in settings.get("extensions", [])
                      if item != str(integration._pi_entry(config_dir))]
        if entry is not None:
            extensions.append(str(entry))
        settings["extensions"] = extensions
        safe_files.write_managed_json_atomic(path, settings, uid=identity.uid, gid=identity.gid, label="Pi 记忆插件")
        return env, None


class DshNativeMemoryAdapter:
    @staticmethod
    def apply(integration, config_dir, identity, env, grant, launcher):
        import json
        from agent_bridge.dsh import plugins

        installed = PACKAGE_SPEC in plugins.read_installed_specs(config_dir)
        if grant and not installed:
            raise ValidationError("Hindsight DSH 原生插件尚未安装完成，请重试启动")
        if not installed:
            return env, None
        path = config_dir / "agent-bridge-hindsight.patch.json"
        if not safe_files.write_text_secure(path, json.dumps([{"id": "hindsight", "disabled": grant is None}]),
                                            uid=identity.uid, gid=identity.gid, label="DSH 记忆覆盖"):
            raise ValidationError("Hindsight DSH 覆盖配置写入失败")
        return env, path


NATIVE_ADAPTERS = {"pi": PiNativeMemoryAdapter(), "dsh": DshNativeMemoryAdapter()}
