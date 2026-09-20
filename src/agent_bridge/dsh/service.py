"""用户级 DSH Web Runtime 生命周期管理。

每个业务用户至多一个 DSH Web 实例：按需启动、动态端口、健康探测、显式
停止、空闲自动回收与服务重启后的遗留实例识别。进程以业务用户所属 group
映射出的 Linux uid/gid 运行，用户级配置目录固定在该 Linux 用户的
``<home>/.config/dsh/<business-user>/`` 下，不进入 Agent Bridge 数据目录。

进程生命周期语义与 claude-mem worker 保持一致：state 文件记录 pid/port/
访问时间，SIGTERM→SIGKILL 升级回收，按进程组发信号。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import signal
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from agent_bridge.agent_runtime.service import DEFAULT_MCP_URL
from agent_bridge.access_control.resources import ScopedResourceType
from agent_bridge.core.domain import ValidationError, require_admin_user
from agent_bridge.core.timeutil import utc_iso
from agent_bridge.dsh import injection
from agent_bridge.dsh.launcher import (
    DshProcessLauncher,
    LinuxIdentity,
    PopenDshLauncher,
    resolve_linux_identity,
)
from agent_bridge.dsh.workspace import (
    DshWorkspaceCapability,
    DshWorkspaceCapabilityRegistry,
    WorkspaceSelection,
    build_mcp_overlay,
    ensure_owner as ensure_overlay_owner,
    mcp_overlay_path as workspace_overlay_path,
    remove_mcp_overlay,
    write_mcp_overlay,
)

logger = logging.getLogger(__name__)

DSH_HOST = "127.0.0.1"
DSH_PORT_BASE = 48400
DSH_MAX_INSTANCES = 50
DSH_STARTUP_TIMEOUT_SECONDS = 60.0
DSH_STOP_GRACE_SECONDS = 5.0
REAP_INTERVAL_SECONDS = 60.0

STATE_DIR_NAME = "dsh-runtimes"
LOG_DIR_NAME = "dsh-runtimes"

_USER_ID_PATTERN = re.compile(r"^[^/\\\s]+$")
# DSH 启动横幅：``dsh web: http://127.0.0.1:<port>/?token=<launch-token>``（可能附带 LAN 地址）
_DSH_WEB_URL_RE = re.compile(r"dsh web:\s*(?P<url>https?://[^\s()]+)")


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def _epoch_to_iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return utc_iso(datetime.fromtimestamp(float(epoch), tz=UTC))


class DshRuntimeService:
    """按业务用户托管 DSH Web 实例，并提供状态查询与空闲回收。"""

    def __init__(
        self,
        *,
        paths,
        configs,
        access,
        admins: set[str],
        launcher: DshProcessLauncher | None = None,
        passwd_lookup: Callable[[str], Any] | None = None,
        mcp_url: str | None = None,
    ) -> None:
        self.paths = paths
        self.admins = admins
        self.mcp_url = mcp_url or DEFAULT_MCP_URL
        self._configs = configs
        self._access = access
        self._launcher = launcher or PopenDshLauncher()
        self._passwd_lookup = passwd_lookup
        self.capabilities = DshWorkspaceCapabilityRegistry()
        self._lock = threading.RLock()
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        # 本进程启动的子进程句柄；用于停止时 wait() 回收，避免僵尸进程被
        # kill(pid, 0) 误判为存活而升级 SIGKILL。重启后经状态文件识别的
        # 遗留 pid 不在此列，走信号回退路径。
        self._processes: dict[int, Any] = {}

    # -- 对外生命周期入口 --

    def ensure_running(
        self, user_id: str, *, workspace: WorkspaceSelection | None = None
    ) -> dict[str, Any]:
        """确保业务用户的 DSH Web 存活：健康则复用，缺失/异常则（重）启动。

        传入 ``workspace`` 时按所选能力平面注入 MCP；``profile_key`` 为空表示
        不注入任何能力（等价于清空既有注入）。已在运行但能力平面不同（含
        “从有到无”）的实例会被回收重启，选择相同则复用并刷新注入配置。
        """
        normalized_user = self._require_user_id(user_id)
        group_key = self._access.actor_group_key(normalized_user, required=True)
        config = self._configs.group_config_for_runtime(group_key)
        if config is None:
            raise ValidationError(f"小组 {group_key} 尚未配置 DSH，请先在系统管理页完成组级配置")

        with self._lock:
            state = self._read_state(normalized_user)
            if state:
                group_changed = str(state.get("group_key") or "") != group_key
                profile_changed = (
                    workspace is not None
                    and (state.get("profile_key") or None) != workspace.profile_key
                )
                alive = self._pid_alive(int(state.get("pid") or 0))
                healthy = alive and self._probe_port(int(state.get("port") or 0))
                if not group_changed and not profile_changed and healthy:
                    self._touch_state(normalized_user)
                    if workspace is not None:
                        self._refresh_workspace_injection(normalized_user, group_key, config, workspace)
                    logger.info(
                        "DSH Runtime 复用存量实例 user=%s pid=%s port=%s",
                        normalized_user,
                        state.get("pid"),
                        state.get("port"),
                    )
                    return self._public_status(self._compute_status(normalized_user, group_key))
                wedged = (
                    not group_changed
                    and not profile_changed
                    and alive
                    and not healthy
                    and (time.time() - float(state.get("started_at") or 0)) < DSH_STARTUP_TIMEOUT_SECONDS
                )
                if wedged:
                    return self._public_status(self._compute_status(normalized_user, group_key))
                # 进程退出、启动超时仍不健康、用户换组或切换能力平面：回收后重启。
                if alive:
                    logger.warning(
                        "DSH Runtime 回收后重启 user=%s pid=%s 原因=%s",
                        normalized_user,
                        state.get("pid"),
                        "切换能力平面" if profile_changed else ("换组" if group_changed else "启动超时或进程退出"),
                    )
                    self._stop_state(state, normalized_user)

            state = self._start_runtime(normalized_user, group_key, config, workspace=workspace)
            return self._public_status(self._compute_status(normalized_user, group_key))

    def authorize_workspace(self, user_id: str, *, profile_key: str | None) -> dict[str, Any]:
        """选定能力平面（可不选）并确保工作台可用。

        选择 Profile 时先按既有资源读取规则校验权限，再签发用户唯一的短期
        capability 并注入 MCP；不选 Profile 时清空注入，工作台不带任何 MCP。
        """
        normalized_user = self._require_user_id(user_id)
        cleaned_profile = str(profile_key or "").strip()
        if not cleaned_profile:
            self.capabilities.revoke_for_user(normalized_user)
            status = self.ensure_running(
                normalized_user, workspace=WorkspaceSelection(profile_key=None)
            )
            logger.info("DSH Workspace 进入（不注入能力平面）user=%s", normalized_user)
            return {**status, "profile_key": None, "workspace_url": "/agent-workspace/"}

        group_key = self._access.actor_group_key(normalized_user, required=True)
        # 切换到无权限 Profile 会被拒绝：沿既有资源读取规则校验能力平面。
        self._access.require_resource_read(
            actor=normalized_user,
            resource_type=ScopedResourceType.capability_profile,
            resource_key=cleaned_profile,
        )
        capability = self.capabilities.issue(
            user_id=normalized_user,
            profile_key=cleaned_profile,
            owner_group_key=group_key,
        )
        status = self.ensure_running(
            normalized_user,
            workspace=WorkspaceSelection(profile_key=cleaned_profile, capability=capability),
        )
        logger.info(
            "DSH Workspace 授权完成 user=%s profile=%s group=%s runtime_status=%s",
            normalized_user,
            cleaned_profile,
            group_key,
            status.get("status"),
        )
        return {**status, "profile_key": cleaned_profile, "workspace_url": "/agent-workspace/"}

    def require_workspace_capability(
        self, token: str, *, profile_key: str | None = None
    ) -> DshWorkspaceCapability:
        """MetaMCP 入口校验 DSH capability 并返回绑定的授权上下文。"""
        return self.capabilities.require(token, profile_key=profile_key)

    def runtime_status(self, user_id: str) -> dict[str, Any]:
        """查询业务用户 runtime 状态；不包含 pid/端口等内部细节。"""
        normalized_user = self._require_user_id(user_id)
        group_key = self._access.actor_group_key(normalized_user, required=False)
        return self._public_status(self._compute_status(normalized_user, group_key))

    def stop_runtime(self, user_id: str) -> dict[str, Any]:
        """停止业务用户 runtime；保留其 DSH 配置与 session 数据。"""
        normalized_user = self._require_user_id(user_id)
        with self._lock:
            state = self._read_state(normalized_user)
            stopped = self._stop_state(state, normalized_user) if state else False
            self.capabilities.revoke_for_user(normalized_user)
        return {"user_id": normalized_user, "stopped": bool(stopped)}

    def touch_runtime(self, user_id: str) -> None:
        """刷新访问时间，供代理请求与显式保活调用。"""
        normalized_user = self._require_user_id(user_id)
        with self._lock:
            self._touch_state(normalized_user)

    def list_runtimes(self, actor: str) -> list[dict[str, Any]]:
        """管理端全量视图：包含 pid、端口、日志路径等内部细节。"""
        require_admin_user(actor, self.admins)
        return [self._compute_status_by_state(state) for state in self._all_states()]

    def require_runtime_target(self, user_id: str) -> str | None:
        """返回业务用户 runtime 的 localhost base_url；未运行时返回 None。

        代理层用它解析转发目标，目标只能来自已登记的 runtime 状态，
        不接受调用方通过 URL 指定任意端口。
        """
        normalized_user = self._require_user_id(user_id)
        with self._lock:
            state = self._read_state(normalized_user)
            if not state:
                return None
            pid = int(state.get("pid") or 0)
            port = int(state.get("port") or 0)
            if not (pid and port and self._pid_alive(pid) and self._probe_port(port)):
                return None
            self._touch_state(normalized_user)
            return self._base_url_for_port(port)

    def workspace_auth(self, user_id: str) -> tuple[str, str] | None:
        """返回 DSH 启动时打印的鉴权入口路径与查询串，例如 ``("/", "token=…")``。

        DSH Web 只接受带启动 token 的首次导航：``GET /?token=…`` 校验后写入
        会话 Cookie 并 303 回 ``/``，后续请求凭 Cookie 通过。代理层用该入口
        完成首次导航，浏览器无需感知 token。非 DSH 实现或日志缺失时返回 None。
        """
        normalized_user = self._require_user_id(user_id)
        with self._lock:
            state = self._read_state(normalized_user)
            if not state:
                return None
            auth_path = str(state.get("auth_path") or "")
            auth_query = str(state.get("auth_query") or "")
            if auth_path and auth_query:
                return auth_path, auth_query
            captured = self._capture_auth_entry(state)
            if captured is None:
                return None
            state.update(captured)
            self._write_state(normalized_user, state)
            return str(captured["auth_path"]), str(captured["auth_query"])

    def refresh_workspace_auth(self, user_id: str) -> tuple[str, str] | None:
        """重扫运行日志中的最新 ``dsh web:`` 横幅，更新已轮换的鉴权入口。

        DSH 的启动 token 绑定进程内 owner，Connection 重载会静默轮换 token 并
        重印横幅；启动时捕获的入口可能已过期（表现为交换 401）。本方法返回
        按最新横幅刷新后的入口；横幅与已存入口一致时只回读、不落盘。日志缺失
        或无横幅时返回 None，调用方应沿用既有入口继续处理。
        """
        normalized_user = self._require_user_id(user_id)
        with self._lock:
            state = self._read_state(normalized_user)
            if not state:
                return None
            captured = self._capture_auth_entry(state)
            if captured is None:
                return None
            new_path = str(captured["auth_path"])
            new_query = str(captured["auth_query"])
            old_path = str(state.get("auth_path") or "")
            old_query = str(state.get("auth_query") or "")
            if new_path == old_path and new_query == old_query:
                return old_path, old_query
            state.update(captured)
            self._write_state(normalized_user, state)
            logger.info(
                "DSH 启动 token 已轮换，按最新横幅刷新鉴权入口 user=%s",
                normalized_user,
            )
            return new_path, new_query

    # -- 启动与回收 --

    def _start_runtime(
        self,
        user_id: str,
        group_key: str,
        config: dict[str, Any],
        *,
        workspace: WorkspaceSelection | None = None,
    ) -> dict[str, Any]:
        linux_user = str(config.get("linux_user") or group_key)
        identity = resolve_linux_identity(linux_user, self._passwd_lookup)
        config_dir = self._ensure_config_dir(identity, user_id)
        port = self._available_port()
        runtime_config = self._configs.runtime_config_for_runtime()
        model_binding = self._configs.model_binding_for(group_key)
        self._inject_settings(config_dir, identity, model_binding)
        overlay_path = self._write_workspace_overlay(config_dir, identity, user_id, workspace)
        command = self._build_command(
            str(runtime_config.get("web_command") or ""), port, patch_path=overlay_path
        )
        env = self._build_env(identity, config_dir, model_binding)
        log_path = self._log_path(user_id)
        logger.info(
            "DSH Runtime 开始启动 user=%s group=%s linux_user=%s port=%s config_dir=%s",
            user_id,
            group_key,
            linux_user,
            port,
            config_dir,
        )
        try:
            process = self._launcher.start(
                command=command,
                env=env,
                cwd=identity.home if identity.home.is_dir() else config_dir,
                log_path=log_path,
                identity=identity,
            )
        except Exception as exc:
            logger.error(
                "DSH Runtime 启动失败 user=%s group=%s 原因=%s",
                user_id,
                group_key,
                exc,
                exc_info=True,
            )
            raise

        now = time.time()
        state = {
            "user_id": user_id,
            "group_key": group_key,
            "linux_user": linux_user,
            "pid": int(process.pid),
            "port": port,
            "config_dir": str(config_dir),
            "log_path": str(log_path),
            "profile_key": workspace.profile_key if workspace is not None else None,
            "started_at": now,
            "last_access_at": now,
        }
        self._processes[int(process.pid)] = process
        self._write_state(user_id, state)

        if not self._wait_until_ready(port, process=process):
            exit_code = process.poll()
            tail = self._tail_log(log_path)
            self._terminate_pid(int(process.pid), grace_seconds=DSH_STOP_GRACE_SECONDS)
            self._remove_state(user_id)
            if exit_code is not None:
                raise ValidationError(
                    f"DSH Web 进程启动后异常退出（exit={exit_code}），日志尾部：{tail}"
                )
            raise ValidationError(
                f"DSH Web 在 {DSH_STARTUP_TIMEOUT_SECONDS:.0f} 秒内未就绪（127.0.0.1:{port}），日志尾部：{tail}"
            )
        captured = self._capture_auth_entry(state)
        if captured is None:
            logger.warning(
                "DSH Runtime 未在启动日志中找到鉴权入口 user=%s log=%s（工作台将无法自动完成首次鉴权）",
                user_id,
                log_path,
            )
        else:
            state.update(captured)
            self._write_state(user_id, state)
        logger.info(
            "DSH Runtime 就绪 user=%s pid=%s port=%s 耗时=%.1fs",
            user_id,
            process.pid,
            port,
            time.time() - now,
        )
        return state

    def _stop_state(self, state: dict[str, Any], user_id: str) -> bool:
        pid = int(state.get("pid") or 0)
        stopped = self._terminate_pid(pid, grace_seconds=DSH_STOP_GRACE_SECONDS)
        self._remove_state(user_id)
        logger.info(
            "DSH Runtime 已停止 user=%s pid=%s signaled=%s config_dir=%s（用户配置已保留）",
            user_id,
            pid,
            stopped,
            state.get("config_dir"),
        )
        return stopped

    def _wait_until_ready(self, port: int, *, process) -> bool:
        deadline = time.monotonic() + DSH_STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._probe_port(port):
                return True
            if process.poll() is not None:
                logger.error(
                    "DSH Web 子进程启动期退出 port=%s exit_code=%s",
                    port,
                    process.returncode,
                )
                return False
            time.sleep(0.3)
        logger.warning(
            "DSH Web 等待就绪超时 port=%s timeout=%.0fs", port, DSH_STARTUP_TIMEOUT_SECONDS
        )
        return False

    def _probe_port(self, port: int) -> bool:
        if port <= 0:
            return False
        try:
            response = httpx.get(self._base_url_for_port(port) + "/", timeout=1.0)
        except Exception:
            return False
        return response.status_code < 500

    def _available_port(self) -> int:
        occupied = {
            int(state.get("port") or 0)
            for state in self._all_states()
            if self._pid_alive(int(state.get("pid") or 0))
        }
        for candidate in range(DSH_PORT_BASE, DSH_PORT_BASE + DSH_MAX_INSTANCES):
            if candidate in occupied or self._port_in_use(candidate):
                continue
            return candidate
        raise ValidationError(
            "DSH Runtime 本地端口池已耗尽"
            f"（{DSH_PORT_BASE}-{DSH_PORT_BASE + DSH_MAX_INSTANCES - 1}），"
            "请先停止不再使用的实例或联系管理员扩大端口池"
        )

    def _write_workspace_overlay(
        self,
        config_dir: Path,
        identity: LinuxIdentity,
        user_id: str,
        workspace: WorkspaceSelection | None,
    ) -> Path | None:
        """按能力平面写入/清除 DSH ``--patch`` 覆盖文件，返回需注入的路径。"""
        overlay_path = workspace_overlay_path(config_dir)
        if workspace is None:
            # 未指定选择：沿用已存在的注入（例如仅调用 /runtime/ensure 保活）。
            return overlay_path if overlay_path.exists() else None
        capability = workspace.capability
        if not workspace.profile_key or capability is None:
            if overlay_path.exists():
                remove_mcp_overlay(overlay_path)
                logger.info("DSH Workspace 已清除能力平面注入 user=%s", user_id)
            return None
        write_mcp_overlay(
            overlay_path,
            build_mcp_overlay(
                mcp_url=self.mcp_url,
                profile_key=workspace.profile_key,
                capability_token=capability.token,
            ),
        )
        ensure_overlay_owner(overlay_path, uid=identity.uid, gid=identity.gid)
        logger.info(
            "DSH Workspace MCP 覆盖文件已写入 user=%s profile=%s path=%s",
            user_id,
            workspace.profile_key,
            overlay_path,
        )
        return overlay_path

    def _refresh_workspace_injection(
        self,
        user_id: str,
        group_key: str,
        config: dict[str, Any],
        workspace: WorkspaceSelection,
    ) -> None:
        """复用实例时重写覆盖文件，使新签发的 capability 立即生效。"""
        state = self._read_state(user_id) or {}
        config_dir = Path(str(state.get("config_dir") or ""))
        if not config_dir or not config_dir.is_dir():
            logger.warning("DSH Workspace 覆盖文件刷新跳过（配置目录缺失）user=%s", user_id)
            return
        linux_user = str(config.get("linux_user") or group_key)
        identity = resolve_linux_identity(linux_user, self._passwd_lookup)
        self._write_workspace_overlay(config_dir, identity, user_id, workspace)

    def _build_command(self, template: str, port: int, *, patch_path: Path | None = None) -> list[str]:
        """渲染启动命令；``{patch}`` 在注入配置文件时展开为 ``--patch <路径>``。"""
        patch_arg = f'--patch "{patch_path}"' if patch_path is not None else ""
        try:
            rendered = template.format(port=port, patch=patch_arg)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValidationError(
                f"DSH 启动命令模板不合法：{template!r}（仅支持 {{port}} 与 {{patch}} 占位符）"
            ) from exc
        command = shlex.split(rendered)
        if not command:
            raise ValidationError(f"DSH 启动命令模板不合法：{template!r}")
        return command

    def _inject_settings(
        self,
        config_dir: Path,
        identity: LinuxIdentity,
        binding: dict[str, Any],
    ) -> Path | None:
        """把公共接入（Base URL / 模型）与组级默认模型写入 DSH settings.yaml。"""
        written = injection.write_settings(
            config_dir,
            base_url=str(binding.get("base_url") or ""),
            models=list(binding.get("available_models") or []),
            default_model=str(binding.get("default_model") or ""),
        )
        # DSH 会热写该文件保存用户偏好，必须归属目标 Linux 用户（root 运行时）。
        settings_file = injection.settings_path(config_dir)
        if settings_file.exists():
            injection.ensure_owner(settings_file, uid=identity.uid, gid=identity.gid)
        if written is None and not binding.get("base_url"):
            logger.warning(
                "DSH 模型接入未配置 Base URL（全局与公共模型配置均为空）config_dir=%s，DSH 将使用自身默认供应商",
                config_dir,
            )
        elif written is None and not binding.get("available_models"):
            # Base URL 已解析（含公共模型配置回落）但全局可用模型为空：供应商
            # 条目与 apiKeyEnv 均不落盘，组级 API Key 不会生效，必须留痕。
            logger.warning(
                "DSH 模型接入已解析 Base URL（source=%s）但全局可用模型列表为空，"
                "跳过供应商注入，组级 API Key 不会生效 config_dir=%s，请在 DSH 运行配置中补充可用模型",
                str(binding.get("base_url_source") or "-"),
                config_dir,
            )
        return written

    def _build_env(
        self,
        identity: LinuxIdentity,
        config_dir: Path,
        binding: dict[str, Any],
    ) -> dict[str, str]:
        """构造 DSH 进程环境：DSH 只从 settings.yaml 读模型配置，密钥走环境变量。"""
        env = os.environ.copy()
        env["HOME"] = str(identity.home)
        env["USER"] = identity.user
        env["LOGNAME"] = identity.user
        env["DSH_HOME"] = str(config_dir)
        env.update(injection.managed_api_key_env_value(str(binding.get("api_key") or "")))
        return env

    @staticmethod
    def _capture_auth_entry(state: dict[str, Any]) -> dict[str, str] | None:
        """从 DSH 运行日志解析 ``dsh web: http://…/?token=…`` 鉴权入口。

        DSH 会在 token 轮换（Connection 重载）时重印横幅，因此以日志中
        最后一条横幅为当前有效入口。
        """
        log_path = Path(str(state.get("log_path") or ""))
        if not log_path or not log_path.exists():
            return None
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        captured: dict[str, str] | None = None
        for match in _DSH_WEB_URL_RE.finditer(text):
            parsed = urlparse(match.group("url"))
            if not parsed.query:
                continue
            captured = {
                "auth_path": parsed.path or "/",
                "auth_query": parsed.query,
            }
        return captured

    def _ensure_config_dir(self, identity: LinuxIdentity, user_id: str) -> Path:
        """创建 ``<linux home>/.config/dsh/<business-user>/`` 并归属目标用户。

        只对本次新建的目录段执行 chown；已存在的用户目录保持原样。
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
        if user_dir not in created:
            created.append(user_dir)
        running_uid = os.geteuid()
        if running_uid == 0 and identity.uid != running_uid:
            for path in created:
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

    # -- 状态计算 --

    def _compute_status_by_state(self, state: dict[str, Any]) -> dict[str, Any]:
        user_id = str(state.get("user_id") or "")
        group_key = str(state.get("group_key") or "")
        pid = int(state.get("pid") or 0)
        port = int(state.get("port") or 0)
        started_at = float(state.get("started_at") or 0.0)
        last_access_at = float(state.get("last_access_at") or started_at)
        alive = self._pid_alive(pid)
        healthy = alive and self._probe_port(port)
        if healthy:
            status = "running"
        elif alive and (time.time() - started_at) < DSH_STARTUP_TIMEOUT_SECONDS:
            status = "starting"
        elif alive:
            status = "unhealthy"
        else:
            status = "stopped"
        return {
            "user_id": user_id,
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

    def _compute_status(self, user_id: str, group_key: str | None) -> dict[str, Any]:
        state = self._read_state(user_id)
        if state is not None:
            return self._compute_status_by_state(state)
        payload: dict[str, Any] = {
            "user_id": user_id,
            "group_key": group_key,
            "linux_user": "",
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
            "user_id",
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
            name="agent-bridge-dsh-reaper",
            daemon=True,
        )
        self._reaper_thread.start()
        logger.info("DSH Runtime 空闲回收线程已启动 interval=%.0fs", REAP_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._reaper_stop.set()
        thread = self._reaper_thread
        self._reaper_thread = None
        if thread is not None:
            thread.join(timeout=REAP_INTERVAL_SECONDS + 5.0)
        logger.info("DSH Runtime 空闲回收线程已停止")

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(REAP_INTERVAL_SECONDS):
            try:
                self.stop_idle_expired()
            except Exception:
                logger.warning("DSH Runtime 空闲回收扫描失败", exc_info=True)

    def stop_idle_expired(self) -> list[str]:
        """停止超过空闲阈值的实例；返回被停止的用户列表。"""
        timeout_minutes = int(
            self._configs.runtime_config_for_runtime().get("idle_timeout_minutes") or 0
        )
        if timeout_minutes <= 0:
            return []
        cutoff = time.time() - timeout_minutes * 60
        stopped: list[str] = []
        with self._lock:
            for state in self._all_states():
                user_id = str(state.get("user_id") or "")
                last_access_at = float(state.get("last_access_at") or 0.0)
                if last_access_at >= cutoff:
                    continue
                logger.info(
                    "DSH Runtime 空闲回收 user=%s idle_minutes=%.1f threshold=%d",
                    user_id,
                    max(0.0, time.time() - last_access_at) / 60.0,
                    timeout_minutes,
                )
                self._stop_state(state, user_id)
                stopped.append(user_id)
        return stopped

    def recover(self) -> dict[str, Any]:
        """服务启动期识别上一进程遗留的 runtime 实例并清理失效状态。"""
        kept = 0
        cleaned = 0
        with self._lock:
            for state in self._all_states():
                user_id = str(state.get("user_id") or "")
                if self._pid_alive(int(state.get("pid") or 0)):
                    kept += 1
                    logger.info(
                        "识别到存活的 DSH Runtime 遗留实例 user=%s pid=%s port=%s",
                        user_id,
                        state.get("pid"),
                        state.get("port"),
                    )
                else:
                    self._remove_state(user_id)
                    cleaned += 1
                    logger.info(
                        "清理已退出的 DSH Runtime 遗留状态 user=%s pid=%s",
                        user_id,
                        state.get("pid"),
                    )
        return {"kept": kept, "cleaned": cleaned}

    def stop_all(self) -> dict[str, Any]:
        """服务停止期回收全部实例；用户 DSH 配置与 session 数据保留。"""
        stopped = 0
        with self._lock:
            for state in self._all_states():
                user_id = str(state.get("user_id") or "")
                if self._stop_state(state, user_id):
                    stopped += 1
                self.capabilities.revoke_for_user(user_id)
        logger.info("DSH Runtime 全部回收完成 stopped=%d", stopped)
        return {"stopped": stopped}

    # -- 进程与工具方法 --

    @staticmethod
    def _require_user_id(user_id: str) -> str:
        normalized = str(user_id or "").strip()
        if not normalized or not _USER_ID_PATTERN.fullmatch(normalized) or normalized in {".", ".."}:
            raise ValidationError(f"业务用户 ID 不合法：{user_id!r}")
        return normalized

    @staticmethod
    def _base_url_for_port(port: int) -> str:
        return f"http://{DSH_HOST}:{port}"

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
            return sock.connect_ex((DSH_HOST, port)) == 0

    def _terminate_pid(self, pid: int, *, grace_seconds: float) -> bool:
        if pid <= 0 or not self._pid_alive(pid):
            return False
        self._signal_process(pid, signal.SIGTERM)
        self._wait_for_exit(pid, grace_seconds=grace_seconds)
        if self._pid_alive(pid):
            logger.warning("DSH Runtime 优雅退出超时，升级为 SIGKILL pid=%s", pid)
            self._signal_process(pid, signal.SIGKILL)
            self._wait_for_exit(pid, grace_seconds=grace_seconds)
        self._processes.pop(pid, None)
        return True

    def _wait_for_exit(self, pid: int, *, grace_seconds: float) -> None:
        process = self._processes.get(pid)
        if process is not None:
            try:
                process.wait(timeout=max(0.0, grace_seconds))
            except Exception:
                pass
            return
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while time.monotonic() < deadline and self._pid_alive(pid):
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

    def _state_path(self, user_id: str) -> Path:
        return self._state_dir() / f"{_safe_key(user_id)}.json"

    def _state_dir(self) -> Path:
        return self.paths.run_dir / STATE_DIR_NAME

    def _log_path(self, user_id: str) -> Path:
        return self.paths.logs_dir / LOG_DIR_NAME / f"{_safe_key(user_id)}.log"

    def _read_state(self, user_id: str) -> dict[str, Any] | None:
        path = self._state_path(user_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("DSH Runtime 状态文件损坏 user=%s path=%s", user_id, path)
            return None
        return payload if isinstance(payload, dict) and payload.get("pid") else None

    def _write_state(self, user_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _touch_state(self, user_id: str) -> None:
        state = self._read_state(user_id)
        if not state:
            return
        state["last_access_at"] = time.time()
        self._write_state(user_id, state)

    def _remove_state(self, user_id: str) -> None:
        self._state_path(user_id).unlink(missing_ok=True)

    def _all_states(self) -> list[dict[str, Any]]:
        state_dir = self._state_dir()
        if not state_dir.exists():
            return []
        states: list[dict[str, Any]] = []
        for state_path in sorted(state_dir.glob("*.json")):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("DSH Runtime 状态文件损坏，已移除 path=%s", state_path)
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
