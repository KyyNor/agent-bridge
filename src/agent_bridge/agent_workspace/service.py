"""通用 Workspace Runtime：与具体 Agent 无关的进程托管。

每个业务用户（个人）或每个 Linux 用户（共享）至多一个工作台实例：按需启动、
动态端口、健康探测、显式停止、空闲自动回收与服务重启后的遗留实例识别。进程
以业务用户所属 group 映射出的 Linux uid/gid 运行，配置目录落在该 Linux 用户的
home 下，不进入 Agent Bridge 数据目录。

工作空间范围（scope）：

- ``personal``：runtime 身份 = 业务用户，配置目录按业务用户隔离；
- ``shared``：runtime 身份 = Linux 用户（``runtime key = linux_user``），同一
  Linux 用户下全部成员共享同一个实例与配置目录。运行期间能力平面锁定为启动
  时选定的 active profile，后续成员直接进入、不改平面、不产生第二个进程。

与 Agent 无关的部分全部在本模块：状态文件、端口分配、就绪探测、uid 切换、
进程树 + 环境标记回收、空闲回收、``recover`` / ``stop_all``、capability 记账。
后端差异（启动命令、模型与 MCP 注入、就绪判定、鉴权入口捕获）全部在
:class:`~agent_bridge.agent_workspace.adapter.WorkspaceAgentAdapter`。

进程生命周期语义与 claude-mem worker 保持一致：state 文件记录 pid/port/访问
时间，SIGTERM→SIGKILL 升级回收。后端子进程普遍以 detached（独立进程组/会话）
方式启动，主进程组信号覆盖不到，因此回收按「主进程 + 全部后代进程树 + 环境
标记孤儿」的完整集合发信号，见 :mod:`agent_bridge.agent_workspace.process_tree`。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from agent_bridge.access_control.resources import ScopedResourceType
from agent_bridge.agent_workspace import process_tree
from agent_bridge.agent_workspace.adapter import WorkspaceAgentAdapter
from agent_bridge.agent_workspace.capability import (
    WorkspaceCapability,
    WorkspaceCapabilityRegistry,
    WorkspaceSelection,
    shared_runtime_capability_key,
)
from agent_bridge.agent_workspace.launcher import (
    LinuxIdentity,
    resolve_linux_identity,
)
from agent_bridge.agent_workspace.scopes import (
    WORKSPACE_SCOPE_PERSONAL,
    WORKSPACE_SCOPE_SHARED,
    normalize_workspace_scope,
)
from agent_bridge.core.domain import ValidationError, require_admin_user
from agent_bridge.core.timeutil import utc_iso

logger = logging.getLogger(__name__)

# 回收扫描间隔；空闲阈值来自各后端的运行配置。
REAP_INTERVAL_SECONDS = 60.0
# 共享实例的 state/log 子目录：与个人实例的 <user_id>.json 文件名空间隔离。
SHARED_STATE_SUBDIR = "shared"

_USER_ID_PATTERN = re.compile(r"^[^/\\\s]+$")


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def _epoch_to_iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return utc_iso(datetime.fromtimestamp(float(epoch), tz=UTC))


class WorkspaceRuntimeService:
    """按 (agent, scope, runtime key) 托管工作台实例。"""

    def __init__(
        self,
        *,
        adapter: WorkspaceAgentAdapter,
        paths,
        configs,
        access,
        admins: set[str],
        launcher=None,
        passwd_lookup: Callable[[str], Any] | None = None,
        mcp_url: str | None = None,
        state_dir_name: str = "workspace-runtimes",
    ) -> None:
        self.adapter = adapter
        self.paths = paths
        self.admins = admins
        self.mcp_url = mcp_url
        self._configs = configs
        self._access = access
        self._launcher = launcher
        self._passwd_lookup = passwd_lookup
        self._state_dir_name = state_dir_name
        self.capabilities = WorkspaceCapabilityRegistry()
        self._lock = threading.RLock()
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        # 本进程启动的子进程句柄；用于停止时 wait() 回收，避免僵尸进程被
        # kill(pid, 0) 误判为存活而升级 SIGKILL。重启后经状态文件识别的
        # 遗留 pid 不在此列，走信号回退路径。
        self._processes: dict[int, Any] = {}
        # 进程枚举与信号实现可由后端/测试替换（DSH 兼容层与既有测试在
        # ``agent_bridge.dsh.process_tree`` 上 monkeypatch）。
        self._process_tree = process_tree

    # -- 对外生命周期入口 --

    def ensure_running(
        self,
        user_id: str,
        *,
        workspace: WorkspaceSelection | None = None,
        scope: str = WORKSPACE_SCOPE_PERSONAL,
    ) -> dict[str, Any]:
        """确保目标工作台存活：健康则复用，缺失/异常则（重）启动。"""
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        self._require_scope_supported(scope)
        group_key = self._access.actor_group_key(normalized_user, required=True)
        config = self._group_config(group_key)
        linux_user = self._linux_user(config, group_key)
        runtime_key = self._runtime_key(scope, normalized_user, linux_user)

        with self._lock:
            state = self._read_state(scope, runtime_key)
            if state:
                # 共享实例按 Linux 用户身份解析：不同成员进入都复用现有进程，
                # 不因 group/profile 变化重启。
                group_changed = (
                    scope != WORKSPACE_SCOPE_SHARED
                    and str(state.get("group_key") or "") != group_key
                )
                profile_changed = (
                    scope != WORKSPACE_SCOPE_SHARED
                    and workspace is not None
                    and (state.get("profile_key") or None) != workspace.profile_key
                )
                alive = self._pid_alive(int(state.get("pid") or 0))
                healthy = alive and self._probe_port(int(state.get("port") or 0))
                if not group_changed and not profile_changed and healthy:
                    self._touch_state(scope, runtime_key)
                    logger.info(
                        "%s Runtime 复用存量实例 scope=%s runtime=%s pid=%s port=%s user=%s",
                        self.adapter.display_name,
                        scope,
                        runtime_key,
                        state.get("pid"),
                        state.get("port"),
                        normalized_user,
                    )
                    return self._public_status(
                        self._compute_status(scope, runtime_key, group_key)
                    )
                wedged = (
                    not group_changed
                    and not profile_changed
                    and alive
                    and not healthy
                    and (time.time() - float(state.get("started_at") or 0))
                    < self.adapter.startup_timeout_seconds
                )
                if wedged:
                    return self._public_status(
                        self._compute_status(scope, runtime_key, group_key)
                    )
                # 进程退出、启动超时仍不健康、用户换组或（仅个人模式）切换能力
                # 平面：回收后重启。
                if alive:
                    logger.warning(
                        "%s Runtime 回收后重启 scope=%s runtime=%s user=%s pid=%s 原因=%s",
                        self.adapter.display_name,
                        scope,
                        runtime_key,
                        normalized_user,
                        state.get("pid"),
                        "切换能力平面"
                        if profile_changed
                        else ("换组" if group_changed else "启动超时或进程退出"),
                    )
                    self._stop_state(state, scope, runtime_key)

            state = self._start_runtime(
                normalized_user,
                group_key,
                config,
                workspace=self._resume_shared_workspace(
                    state, scope, group_key, linux_user, workspace
                ),
                scope=scope,
                runtime_key=runtime_key,
            )
            return self._public_status(self._compute_status(scope, runtime_key, group_key))

    def authorize_workspace(
        self,
        user_id: str,
        *,
        profile_key: str | None,
        scope: str = WORKSPACE_SCOPE_PERSONAL,
    ) -> dict[str, Any]:
        """选定工作空间范围与能力平面（可不选）并确保工作台可用。"""
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        self._require_scope_supported(scope)
        cleaned_profile = str(profile_key or "").strip()

        if scope == WORKSPACE_SCOPE_SHARED:
            return self._authorize_shared_workspace(normalized_user, profile_key=cleaned_profile)

        if not cleaned_profile:
            self.capabilities.revoke_for_user(normalized_user)
            status = self.ensure_running(
                normalized_user, workspace=WorkspaceSelection(profile_key=None)
            )
            logger.info(
                "%s 工作台进入（不注入能力平面）user=%s", self.adapter.display_name, normalized_user
            )
            return {**status, "profile_key": None, "workspace_url": self.workspace_url(scope)}

        group_key = self._access.actor_group_key(normalized_user, required=True)
        # 切换到无权限 Profile 会被拒绝：沿既有资源读取规则校验能力平面。
        self._access.require_resource_read(
            actor=normalized_user,
            resource_type=ScopedResourceType.capability_profile,
            resource_key=cleaned_profile,
        )
        with self._lock:
            state = self._read_state(WORKSPACE_SCOPE_PERSONAL, normalized_user)
            capability = self.capabilities.current_for_user(normalized_user)
            reusable = bool(
                state
                and capability
                and self._pid_alive(int(state.get("pid") or 0))
                and self._probe_port(int(state.get("port") or 0))
                and state.get("profile_key") == cleaned_profile
                and state.get("group_key") == group_key
                and capability.profile_key == cleaned_profile
                and capability.owner_group_key == group_key
                and state.get("capability_digest")
                == hashlib.sha256(capability.token.encode()).hexdigest()
            )
            if reusable:
                # 活进程已加载该 token，不改注入，避免触发后端配置重载。
                status = self.ensure_running(normalized_user)
            else:
                # 后端在启动时加载注入配置。旧进程即使看到磁盘上的新 token，
                # MCP client 仍可能持有旧 token；重签前必须停止旧进程。
                if state:
                    self._stop_state(state, WORKSPACE_SCOPE_PERSONAL, normalized_user)
                capability = self.capabilities.issue(
                    user_id=normalized_user,
                    profile_key=cleaned_profile,
                    owner_group_key=group_key,
                )
                status = self.ensure_running(
                    normalized_user,
                    workspace=WorkspaceSelection(
                        profile_key=cleaned_profile, capability=capability
                    ),
                )
        logger.info(
            "%s 工作台授权完成 user=%s profile=%s group=%s runtime_status=%s",
            self.adapter.display_name,
            normalized_user,
            cleaned_profile,
            group_key,
            status.get("status"),
        )
        return {**status, "profile_key": cleaned_profile, "workspace_url": self.workspace_url(scope)}

    def _authorize_shared_workspace(
        self, user_id: str, *, profile_key: str
    ) -> dict[str, Any]:
        """小组共享工作台的进入语义：运行中锁定 active profile，未运行按选择启动。

        共享 Runtime 使用 **runtime 级稳定 capability**：启动时按
        ``linux_user + active_profile`` 签发（user_id = Linux 用户，MCP 调用
        以共享 runtime/Linux 用户身份审计）并写入共享注入配置；后续成员进入
        只校验对 active profile 的访问权限，**不重签发、不重写配置**。

        全程持有服务锁（RLock 重入）：「读取状态 → 决定 effective profile →
        启动/复用」对并发进入原子化。
        """
        group_key = self._access.actor_group_key(user_id, required=True)
        config = self._group_config(group_key)
        linux_user = self._linux_user(config, group_key)

        with self._lock:
            state = self._read_state(WORKSPACE_SCOPE_SHARED, linux_user)
            running = bool(state) and self._pid_alive(int(state.get("pid") or 0))
            if running:
                active_profile = (state.get("profile_key") or None) if state else None
                if active_profile:
                    # 共享平面是 runtime 属性：无权访问 active profile 的成员
                    # 不得进入（capability 以 runtime/Linux 用户身份审计，成员
                    # 权限只在进入口校验）。
                    self._access.require_resource_read(
                        actor=user_id,
                        resource_type=ScopedResourceType.capability_profile,
                        resource_key=str(active_profile),
                    )
                status = self.ensure_running(user_id, scope=WORKSPACE_SCOPE_SHARED)
                logger.info(
                    "%s 共享工作台复用现有 Runtime user=%s shared=%s profile=%s",
                    self.adapter.display_name,
                    user_id,
                    linux_user,
                    active_profile,
                )
                return {
                    **status,
                    "profile_key": active_profile,
                    "workspace_url": self.workspace_url("shared"),
                }

            # 未运行：本次请求（首位成员）决定下一次共享 Runtime 的 active profile。
            effective_profile = profile_key or None
            if effective_profile:
                self._access.require_resource_read(
                    actor=user_id,
                    resource_type=ScopedResourceType.capability_profile,
                    resource_key=str(effective_profile),
                )
                runtime_capability = self.capabilities.issue(
                    user_id=linux_user,
                    profile_key=str(effective_profile),
                    owner_group_key=group_key,
                    # 不设独立过期：capability 绑定共享 Runtime 生命周期。
                    ttl_seconds=None,
                    key=shared_runtime_capability_key(linux_user),
                )
                workspace: WorkspaceSelection | None = WorkspaceSelection(
                    profile_key=str(effective_profile), capability=runtime_capability
                )
            else:
                workspace = WorkspaceSelection(profile_key=None)
            status = self.ensure_running(
                user_id, workspace=workspace, scope=WORKSPACE_SCOPE_SHARED
            )
        logger.info(
            "%s 共享工作台授权完成 user=%s shared=%s profile=%s group=%s runtime_status=%s",
            self.adapter.display_name,
            user_id,
            linux_user,
            effective_profile,
            group_key,
            status.get("status"),
        )
        return {
            **status,
            "profile_key": effective_profile,
            "workspace_url": self.workspace_url("shared"),
        }

    def _resume_shared_workspace(
        self,
        state: dict[str, Any] | None,
        scope: str,
        group_key: str,
        linux_user: str,
        workspace: WorkspaceSelection | None,
    ) -> WorkspaceSelection | None:
        """共享 Runtime 重启（进程退出/unhealthy 回收）时恢复 active profile。"""
        if (
            scope != WORKSPACE_SCOPE_SHARED
            or workspace is not None
            or state is None
        ):
            return workspace
        profile = str(state.get("profile_key") or "")
        if not profile:
            return WorkspaceSelection(profile_key=None)
        # 归属组沿用启动该实例时的组（可能与本成员当前组不同，审计保持一致）。
        owner_group_key = str(state.get("group_key") or group_key)
        capability = self.capabilities.issue(
            user_id=linux_user,
            profile_key=profile,
            owner_group_key=owner_group_key,
            ttl_seconds=None,
            key=shared_runtime_capability_key(linux_user),
        )
        logger.info(
            "%s 共享 Runtime 重启恢复 active profile shared=%s profile=%s",
            self.adapter.display_name,
            linux_user,
            profile,
        )
        return WorkspaceSelection(profile_key=profile, capability=capability)

    def require_workspace_capability(
        self, token: str, *, profile_key: str | None = None
    ) -> WorkspaceCapability:
        """MCP 入口校验工作台 capability 并返回绑定的授权上下文。"""
        return self.capabilities.require(token, profile_key=profile_key)

    def runtime_status(
        self, user_id: str, *, scope: str = WORKSPACE_SCOPE_PERSONAL
    ) -> dict[str, Any]:
        """查询业务用户的目标 runtime 状态；不包含 pid/端口等内部细节。"""
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        group_key = self._access.actor_group_key(normalized_user, required=False)
        runtime_key, resolved_group = self._resolve_runtime_key(
            normalized_user, scope, group_key
        )
        return self._public_status(
            self._compute_status(scope, runtime_key, resolved_group)
        )

    def stop_runtime(
        self, user_id: str, *, scope: str = WORKSPACE_SCOPE_PERSONAL
    ) -> dict[str, Any]:
        """停止目标 runtime；保留其配置与 session 数据。"""
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            stopped = self._stop_state(state, scope, runtime_key) if state else False
            if scope == WORKSPACE_SCOPE_PERSONAL:
                # 只撤销个人 capability；共享 Runtime 的 capability 在
                # _stop_state 内按 runtime 槽位撤销。
                self.capabilities.revoke_for_user(normalized_user)
        return {
            "agent": self.adapter.slug,
            "user_id": normalized_user,
            "scope": scope,
            "runtime_key": runtime_key,
            "stopped": bool(stopped),
        }

    def touch_runtime(
        self, user_id: str, *, scope: str = WORKSPACE_SCOPE_PERSONAL
    ) -> None:
        """刷新访问时间，供代理请求与显式保活调用。"""
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            self._touch_state(scope, runtime_key)

    def list_runtimes(self, actor: str) -> list[dict[str, Any]]:
        """管理端全量视图：包含 pid、端口、日志路径等内部细节。"""
        require_admin_user(actor, self.admins)
        return [self._compute_status_by_state(state) for state in self._all_states()]

    def require_runtime_target(
        self, user_id: str, *, scope: str = WORKSPACE_SCOPE_PERSONAL
    ) -> str | None:
        """返回目标 runtime 的 localhost base_url；未运行时返回 None。

        代理层用它解析转发目标，目标只能来自已登记的 runtime 状态，不接受
        调用方通过 URL 指定任意端口。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            if not state:
                return None
            pid = int(state.get("pid") or 0)
            port = int(state.get("port") or 0)
            if not (pid and port and self._pid_alive(pid) and self._probe_port(port)):
                return None
            self._touch_state(scope, runtime_key)
            return self._base_url_for_port(port)

    def workspace_url(self, scope: str) -> str:
        """该范围的浏览器入口前缀（代理前缀 + 尾斜杠）。"""
        if scope == WORKSPACE_SCOPE_SHARED:
            return self.adapter.shared_proxy_prefix + "/"
        return self.adapter.personal_proxy_prefix + "/"

    # -- 启动与回收 --

    def _require_scope_supported(self, scope: str) -> None:
        if not self.adapter.supports_scope(scope):
            raise ValidationError(
                f"{self.adapter.display_name} 暂不支持工作空间范围：{scope}"
            )

    def _group_config(self, group_key: str) -> dict[str, Any]:
        config = self._configs.group_config_for_runtime(group_key)
        if config is None:
            raise ValidationError(
                f"小组 {group_key} 尚未配置 {self.adapter.display_name}，请先在系统管理页完成组级配置"
            )
        return config

    @staticmethod
    def _linux_user(config: dict[str, Any], group_key: str) -> str:
        return str(config.get("linux_user") or group_key)

    @staticmethod
    def _runtime_key(scope: str, user_id: str, linux_user: str) -> str:
        """runtime 状态身份：个人 = 业务用户，共享 = Linux 用户。"""
        if scope == WORKSPACE_SCOPE_SHARED:
            return linux_user
        return user_id

    def _resolve_runtime_key(
        self, user_id: str, scope: str, group_key: str | None
    ) -> tuple[str, str | None]:
        """把业务用户解析为 (runtime_key, group_key)。"""
        if scope != WORKSPACE_SCOPE_SHARED:
            return user_id, group_key
        if group_key is None:
            group_key = self._access.actor_group_key(user_id, required=False)
        if group_key is None:
            return user_id, None
        config = self._configs.group_config_for_runtime(group_key)
        if config is None:
            return user_id, group_key
        return self._linux_user(config, group_key), group_key

    def _start_runtime(
        self,
        user_id: str,
        group_key: str,
        config: dict[str, Any],
        *,
        workspace: WorkspaceSelection | None = None,
        scope: str = WORKSPACE_SCOPE_PERSONAL,
        runtime_key: str | None = None,
    ) -> dict[str, Any]:
        total_started = time.monotonic()
        linux_user = self._linux_user(config, group_key)
        if runtime_key is None:
            runtime_key = self._runtime_key(scope, user_id, linux_user)
        identity = resolve_linux_identity(linux_user, self._passwd_lookup)
        runtime_owner = linux_user if scope == WORKSPACE_SCOPE_SHARED else user_id
        config_dir = self.adapter.config_dir(identity, runtime_owner)
        port = self._available_port()
        model_binding = self._configs.model_binding_for(group_key)
        log_path = self._log_path(scope, runtime_key)
        mark = process_tree.runtime_mark_value(scope, runtime_key)

        logger.info(
            "%s Runtime 开始启动 scope=%s runtime=%s user=%s group=%s linux_user=%s"
            " port=%s config_dir=%s",
            self.adapter.display_name,
            scope,
            runtime_key,
            user_id,
            group_key,
            linux_user,
            port,
            config_dir,
        )
        # adapter 若持有 launcher（如 DSH 插件首装），在构建启动计划**之前**
        # 同步为服务当前实例：既有用例通过替换 ``service.dsh._launcher`` 注入
        # fake launcher，插件安装发生在 build_launch_plan 内部。
        if hasattr(self.adapter, "_launcher"):
            self.adapter._launcher = self._launcher
        plan = self.adapter.build_launch_plan(
            identity=identity,
            runtime_owner=runtime_owner,
            scope=scope,
            runtime_key=runtime_key,
            port=port,
            workspace=workspace,
            model_binding=model_binding,
            log_path=log_path,
        )
        # 环境标记必须在 plan 之上覆盖：adapter 不应自行决定标记名。
        plan.env[self.adapter.mark_env] = mark
        try:
            process = self._launcher.start(
                command=plan.command,
                env=plan.env,
                cwd=identity.home if identity.home.is_dir() else config_dir,
                log_path=log_path,
                identity=identity,
            )
        except Exception as exc:
            logger.error(
                "%s Runtime 启动失败 scope=%s runtime=%s user=%s group=%s 原因=%s",
                self.adapter.display_name,
                scope,
                runtime_key,
                user_id,
                group_key,
                exc,
                exc_info=True,
            )
            raise

        now = time.time()
        state = {
            "agent": self.adapter.slug,
            "user_id": runtime_key,
            "scope": scope,
            "group_key": group_key,
            "linux_user": linux_user,
            "pid": int(process.pid),
            "port": port,
            "config_dir": str(config_dir),
            "log_path": str(log_path),
            "profile_key": workspace.profile_key if workspace is not None else None,
            "capability_digest": (
                hashlib.sha256(workspace.capability.token.encode()).hexdigest()
                if workspace is not None and workspace.capability is not None
                else None
            ),
            "started_at": now,
            "last_access_at": now,
        }
        if plan.auth_secret:
            # 代理首访鉴权所需的秘密：只落 0600 状态文件，不进日志/用户态响应。
            state["auth_secret"] = plan.auth_secret
        self._processes[int(process.pid)] = process
        self._write_state(scope, runtime_key, state)

        if not self._wait_until_ready(port, process=process, timeout=plan.startup_timeout_seconds):
            exit_code = process.poll()
            tail = self._tail_log(log_path)
            self._terminate_pid(
                int(process.pid),
                grace_seconds=self.adapter.stop_grace_seconds,
                mark=mark,
            )
            self._remove_state(scope, runtime_key)
            if exit_code is not None:
                raise ValidationError(
                    f"{self.adapter.display_name} 进程启动后异常退出（exit={exit_code}），日志尾部：{tail}"
                )
            raise ValidationError(
                f"{self.adapter.display_name} 在 {plan.startup_timeout_seconds:.0f} 秒内未就绪"
                f"（127.0.0.1:{port}），日志尾部：{tail}"
            )
        logger.info(
            "%s Runtime 就绪 scope=%s runtime=%s pid=%s port=%s 总耗时=%.1fs",
            self.adapter.display_name,
            scope,
            runtime_key,
            process.pid,
            port,
            time.monotonic() - total_started,
        )
        return state

    def _stop_state(self, state: dict[str, Any], scope: str, runtime_key: str) -> bool:
        pid = int(state.get("pid") or 0)
        stopped = self._terminate_pid(
            pid,
            grace_seconds=self.adapter.stop_grace_seconds,
            mark=process_tree.runtime_mark_value(scope, runtime_key),
        )
        if scope == WORKSPACE_SCOPE_PERSONAL:
            self.capabilities.revoke_for_user(runtime_key)
        if scope == WORKSPACE_SCOPE_SHARED:
            # 共享 Runtime 的稳定 capability 随 runtime 生命周期撤销；注入
            # 配置内的 token 同步失效，一并移除，避免下次按需启动带着死 token。
            self.capabilities.revoke_by_key(shared_runtime_capability_key(runtime_key))
        try:
            self.adapter.on_stopped(state, scope=scope, runtime_key=runtime_key)
        except Exception:
            logger.warning(
                "%s Runtime 停止后清理注入失败 scope=%s runtime=%s",
                self.adapter.display_name,
                scope,
                runtime_key,
                exc_info=True,
            )
        self._remove_state(scope, runtime_key)
        logger.info(
            "%s Runtime 已停止 scope=%s runtime=%s pid=%s signaled=%s config_dir=%s（用户配置已保留）",
            self.adapter.display_name,
            scope,
            runtime_key,
            pid,
            stopped,
            state.get("config_dir"),
        )
        return stopped

    def _wait_until_ready(self, port: int, *, process, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._probe_port(port):
                return True
            if process.poll() is not None:
                logger.error(
                    "%s 子进程启动期退出 port=%s exit_code=%s",
                    self.adapter.display_name,
                    port,
                    process.returncode,
                )
                return False
            time.sleep(0.3)
        logger.warning(
            "%s 等待就绪超时 port=%s timeout=%.0fs",
            self.adapter.display_name,
            port,
            timeout,
        )
        return False

    def _probe_port(self, port: int) -> bool:
        if port <= 0:
            return False
        try:
            response = httpx.get(
                self._base_url_for_port(port) + self.adapter.probe_path, timeout=1.0
            )
        except Exception:
            return False
        return response.status_code < 500

    def _available_port(self) -> int:
        occupied = {
            int(state.get("port") or 0)
            for state in self._all_states()
            if self._pid_alive(int(state.get("pid") or 0))
        }
        base = self.adapter.port_base
        for candidate in range(base, base + self.adapter.port_pool_size):
            if candidate in occupied or self._port_in_use(candidate):
                continue
            return candidate
        raise ValidationError(
            f"{self.adapter.display_name} Runtime 本地端口池已耗尽"
            f"（{base}-{base + self.adapter.port_pool_size - 1}），"
            "请先停止不再使用的实例或联系管理员扩大端口池"
        )

    # -- 状态计算 --

    def _compute_status_by_state(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime_key = str(state.get("user_id") or "")
        scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
        group_key = str(state.get("group_key") or "")
        pid = int(state.get("pid") or 0)
        port = int(state.get("port") or 0)
        started_at = float(state.get("started_at") or 0.0)
        last_access_at = float(state.get("last_access_at") or started_at)
        alive = self._pid_alive(pid)
        healthy = alive and self._probe_port(port)
        if healthy:
            status = "running"
        elif alive and (time.time() - started_at) < self.adapter.startup_timeout_seconds:
            status = "starting"
        elif alive:
            status = "unhealthy"
        else:
            status = "stopped"
        return {
            "agent": self.adapter.slug,
            "user_id": runtime_key,
            "scope": scope,
            "group_key": group_key,
            "linux_user": str(state.get("linux_user") or ""),
            "config_dir": str(state.get("config_dir") or ""),
            "status": status,
            "profile_key": state.get("profile_key"),
            "started_at": _epoch_to_iso(started_at),
            "last_access_at": _epoch_to_iso(last_access_at),
            "idle_minutes": round(max(0.0, time.time() - last_access_at) / 60.0, 1),
            "pid": pid if alive else None,
            "port": port,
            "base_url": self._base_url_for_port(port),
            "log_path": str(state.get("log_path") or ""),
        }

    def _compute_status(
        self, scope: str, runtime_key: str, group_key: str | None
    ) -> dict[str, Any]:
        state = self._read_state(scope, runtime_key)
        if state is not None:
            return self._compute_status_by_state(state)
        linux_user = ""
        if scope == WORKSPACE_SCOPE_SHARED and group_key:
            config = self._configs.group_config_for_runtime(group_key)
            if config is not None:
                linux_user = self._linux_user(config, group_key)
        payload: dict[str, Any] = {
            "agent": self.adapter.slug,
            "user_id": runtime_key,
            "scope": scope,
            "group_key": group_key,
            "linux_user": linux_user,
            "config_dir": "",
            "status": "stopped",
            "profile_key": None,
            "started_at": None,
            "last_access_at": None,
            "idle_minutes": None,
            "pid": None,
            "port": 0,
            "base_url": "",
            "log_path": "",
        }
        if group_key is None:
            payload["status"] = "unassigned"
        elif self._configs.group_config_for_runtime(group_key) is None:
            payload["status"] = "unconfigured"
        return payload

    @staticmethod
    def _public_status(status: dict[str, Any]) -> dict[str, Any]:
        """面向业务用户的状态视图：不暴露动态端口与进程细节。"""
        keys = (
            "agent",
            "user_id",
            "scope",
            "group_key",
            "linux_user",
            "config_dir",
            "status",
            "profile_key",
            "started_at",
            "last_access_at",
            "idle_minutes",
        )
        return {key: status.get(key) for key in keys}

    # -- 空闲回收与服务启停 --

    def start(self) -> None:
        """启动空闲回收线程；服务启动期调用。"""
        if self._reaper_thread is not None:
            return
        self._reaper_stop.clear()
        self._reaper_thread = threading.Thread(
            target=self._reap_loop,
            name=f"agent-bridge-{self.adapter.slug}-reaper",
            daemon=True,
        )
        self._reaper_thread.start()
        logger.info(
            "%s Runtime 空闲回收线程已启动 interval=%.0fs",
            self.adapter.display_name,
            REAP_INTERVAL_SECONDS,
        )

    def stop(self) -> None:
        self._reaper_stop.set()
        thread = self._reaper_thread
        self._reaper_thread = None
        if thread is not None:
            thread.join(timeout=REAP_INTERVAL_SECONDS + 5.0)
        logger.info("%s Runtime 空闲回收线程已停止", self.adapter.display_name)

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(REAP_INTERVAL_SECONDS):
            try:
                self.stop_idle_expired()
            except Exception:
                logger.warning(
                    "%s Runtime 空闲回收扫描失败", self.adapter.display_name, exc_info=True
                )

    def stop_idle_expired(self) -> list[str]:
        """停止超过空闲阈值的实例；返回被停止的 runtime 标识列表。"""
        timeout_minutes = int(
            self._configs.runtime_config_for_runtime().get("idle_timeout_minutes") or 0
        )
        if timeout_minutes <= 0:
            return []
        cutoff = time.time() - timeout_minutes * 60
        stopped: list[str] = []
        with self._lock:
            for state in self._all_states():
                scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
                runtime_key = str(state.get("user_id") or "")
                last_access_at = float(state.get("last_access_at") or 0.0)
                if last_access_at >= cutoff:
                    continue
                logger.info(
                    "%s Runtime 空闲回收 scope=%s runtime=%s idle_minutes=%.1f threshold=%d",
                    self.adapter.display_name,
                    scope,
                    runtime_key,
                    max(0.0, time.time() - last_access_at) / 60.0,
                    timeout_minutes,
                )
                self._stop_state(state, scope, runtime_key)
                stopped.append(runtime_key)
        return stopped

    def recover(self) -> dict[str, Any]:
        """服务启动期识别上一进程遗留的 runtime 实例并清理失效状态。

        共享 Runtime 的稳定 capability 只存在于 Agent Bridge 进程内存，而
        注入配置里的 token 持久留在配置目录：跨进程恢复的共享实例必然持有
        死 token。因此对存活共享实例直接安全停止并清理 state/注入文件，让
        首位成员下次进入时重新按 active profile 启动并生成新 capability；个人
        实例维持「存活保留」语义。
        """
        kept = 0
        cleaned = 0
        stopped_shared = 0
        with self._lock:
            for state in self._all_states():
                scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
                runtime_key = str(state.get("user_id") or "")
                alive = self._pid_alive(int(state.get("pid") or 0))
                if scope == WORKSPACE_SCOPE_SHARED:
                    self._stop_state(state, scope, runtime_key)
                    stopped_shared += 1
                    logger.info(
                        "%s 共享 Runtime 遗留实例已回收（token 不可跨进程复用，待成员重新进入）"
                        " shared=%s pid=%s alive=%s",
                        self.adapter.display_name,
                        runtime_key,
                        state.get("pid"),
                        alive,
                    )
                    continue
                if alive:
                    kept += 1
                    logger.info(
                        "识别到存活的 %s Runtime 遗留实例 scope=%s runtime=%s pid=%s port=%s",
                        self.adapter.display_name,
                        scope,
                        runtime_key,
                        state.get("pid"),
                        state.get("port"),
                    )
                else:
                    self._remove_state(scope, runtime_key)
                    cleaned += 1
                    logger.info(
                        "清理已退出的 %s Runtime 遗留状态 scope=%s runtime=%s pid=%s",
                        self.adapter.display_name,
                        scope,
                        runtime_key,
                        state.get("pid"),
                    )
        return {"kept": kept, "cleaned": cleaned, "stopped_shared": stopped_shared}

    def stop_all(self) -> dict[str, Any]:
        """服务停止期回收全部实例（含共享）；用户配置与 session 数据保留。"""
        stopped = 0
        with self._lock:
            for state in self._all_states():
                scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
                runtime_key = str(state.get("user_id") or "")
                if self._stop_state(state, scope, runtime_key):
                    stopped += 1
            self.capabilities.revoke_all()
        logger.info("%s Runtime 全部回收完成 stopped=%d", self.adapter.display_name, stopped)
        return {"stopped": stopped}

    # -- 进程与状态文件工具 --

    @staticmethod
    def _require_user_id(user_id: str) -> str:
        normalized = str(user_id or "").strip()
        if not normalized or not _USER_ID_PATTERN.fullmatch(normalized) or normalized in {".", ".."}:
            raise ValidationError(f"业务用户 ID 不合法：{user_id!r}")
        return normalized

    @staticmethod
    def _base_url_for_port(port: int) -> str:
        return f"http://127.0.0.1:{port}"

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def _terminate_pid(
        self, pid: int, *, grace_seconds: float, mark: str | None = None
    ) -> bool:
        """终止一个 runtime：主进程、全部后代与带标记的孤儿一起回收。"""
        targets = self._collect_runtime_pids(pid, mark)
        if not targets:
            return False
        for target in sorted(targets):
            self._signal_process(target, signal.SIGTERM)
        self._wait_targets_exit(targets, grace_seconds=grace_seconds)
        alive = self._alive_targets(targets)
        if alive:
            alive |= self._collect_runtime_pids(pid, mark)
            logger.warning(
                "%s Runtime 优雅退出超时，升级为 SIGKILL pid=%s 存活=%d pids=%s",
                self.adapter.display_name,
                pid,
                len(alive),
                sorted(alive),
            )
            for target in sorted(alive):
                self._signal_process(target, signal.SIGKILL)
            self._wait_targets_exit(alive, grace_seconds=grace_seconds)
            remaining = self._alive_targets(alive)
            if remaining:
                logger.warning(
                    "%s Runtime SIGKILL 后仍有存活进程 pid=%s pids=%s（请人工排查）",
                    self.adapter.display_name,
                    pid,
                    sorted(remaining),
                )
        self._processes.pop(pid, None)
        return True

    def _collect_runtime_pids(self, pid: int, mark: str | None) -> set[int]:
        """收集一个 runtime 的全部存活进程：主进程后代树 + 环境标记命中的孤儿。"""
        targets: set[int] = set()
        if pid > 0 and self._pid_alive(pid):
            tree = self._process_tree.descendants(pid)
            targets |= tree
            if not tree:
                targets.add(pid)
        if mark:
            targets |= self._process_tree.marked_pids(mark)
        targets = self._process_tree.discard_own_process(targets)
        return {target for target in targets if self._pid_alive(target)}

    def _alive_targets(self, pids: set[int]) -> set[int]:
        """仍在运行的目标；僵尸视为已退出，避免宽限期被僵尸进程拖满。"""
        return {
            pid
            for pid in pids
            if self._pid_alive(pid) and not self._process_tree.process_is_zombie(pid)
        }

    def _wait_targets_exit(self, pids: set[int], *, grace_seconds: float) -> None:
        """等待目标集合退出：本进程启动的句柄先 wait() 收尸，其余轮询存活。"""
        deadline = time.monotonic() + max(0.0, grace_seconds)
        for pid in sorted(pids):
            process = self._processes.get(pid)
            if process is None:
                continue
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass
        while time.monotonic() < deadline:
            if not self._alive_targets(pids):
                return
            time.sleep(0.1)

    @staticmethod
    def _signal_process(pid: int, sig: int) -> None:
        try:
            process_group_id = os.getpgid(pid)
        except OSError:
            process_group_id = pid
        try:
            os.killpg(process_group_id, sig)
        except ProcessLookupError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        except OSError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def _state_path(self, scope: str, runtime_key: str) -> Path:
        """runtime 状态文件：个人 ``<key>.json``，共享 ``shared/<key>.json``。"""
        state_dir = self._state_dir()
        if scope == WORKSPACE_SCOPE_SHARED:
            return state_dir / SHARED_STATE_SUBDIR / f"{_safe_key(runtime_key)}.json"
        return state_dir / f"{_safe_key(runtime_key)}.json"

    def _state_dir(self) -> Path:
        return self.paths.run_dir / self._state_dir_name

    def _log_path(self, scope: str, runtime_key: str) -> Path:
        base = self.paths.logs_dir / self._state_dir_name
        if scope == WORKSPACE_SCOPE_SHARED:
            return base / SHARED_STATE_SUBDIR / f"{_safe_key(runtime_key)}.log"
        return base / f"{_safe_key(runtime_key)}.log"

    def _read_state(self, scope: str, runtime_key: str) -> dict[str, Any] | None:
        path = self._state_path(scope, runtime_key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "%s Runtime 状态文件损坏 scope=%s runtime=%s path=%s",
                self.adapter.display_name,
                scope,
                runtime_key,
                path,
            )
            return None
        return payload if isinstance(payload, dict) and payload.get("pid") else None

    def _write_state(self, scope: str, runtime_key: str, state: dict[str, Any]) -> None:
        path = self._state_path(scope, runtime_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if state.get("auth_secret"):
            # 状态文件可能含首访鉴权秘密，权限收紧到 0600。
            try:
                path.chmod(0o600)
            except OSError:
                logger.warning(
                    "%s Runtime 状态文件 chmod 失败 path=%s",
                    self.adapter.display_name,
                    path,
                )

    def _touch_state(self, scope: str, runtime_key: str) -> None:
        state = self._read_state(scope, runtime_key)
        if not state:
            return
        state["last_access_at"] = time.time()
        self._write_state(scope, runtime_key, state)

    def _remove_state(self, scope: str, runtime_key: str) -> None:
        self._state_path(scope, runtime_key).unlink(missing_ok=True)

    def _all_states(self) -> list[dict[str, Any]]:
        """读取全部 runtime 状态（个人 + 共享子目录）。"""
        state_dir = self._state_dir()
        if not state_dir.exists():
            return []
        state_paths = sorted(state_dir.glob("*.json"))
        shared_dir = state_dir / SHARED_STATE_SUBDIR
        if shared_dir.is_dir():
            state_paths.extend(sorted(shared_dir.glob("*.json")))
        states: list[dict[str, Any]] = []
        for state_path in state_paths:
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning(
                    "%s Runtime 状态文件损坏，已移除 path=%s",
                    self.adapter.display_name,
                    state_path,
                )
                state_path.unlink(missing_ok=True)
                continue
            if isinstance(payload, dict) and payload.get("pid"):
                states.append(payload)
        return states

    @staticmethod
    def _tail_log(log_path: Path, *, max_chars: int = 2000) -> str:
        if not log_path.exists():
            return ""
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        return text[-max_chars:].strip()