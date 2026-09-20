"""DSH Web 子进程启动器。

以业务用户所属 group 映射出的 Linux 用户身份启动 DSH Web 进程：Agent
Bridge 以 root 运行时通过 :meth:`subprocess.Popen` 的 ``user`` / ``group``
参数（由 C 层 ``_posixsubprocess`` 执行 uid/gid 切换，避免多线程下使用
``preexec_fn``）降权；目标用户即当前用户时不切换，便于开发与测试环境
复用同一生命周期语义。
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_bridge.core.domain import AccessDenied, ValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinuxIdentity:
    """group 映射出的 Linux 用户身份及其 home 目录。"""

    user: str
    uid: int
    gid: int
    home: Path


def resolve_linux_identity(linux_user: str, passwd_lookup=None) -> LinuxIdentity:
    """把 Linux 用户名解析为 uid/gid/home；用户不存在时明确报错。"""
    import pwd

    lookup = passwd_lookup or pwd.getpwnam
    try:
        entry = lookup(linux_user)
    except KeyError as exc:
        raise ValidationError(f"Linux 用户不存在：{linux_user}") from exc
    return LinuxIdentity(
        user=linux_user,
        uid=int(entry.pw_uid),
        gid=int(entry.pw_gid),
        home=Path(str(entry.pw_dir)),
    )


@runtime_checkable
class DshProcessHandle(Protocol):
    """DSH Web 子进程句柄：可查询 pid 与退出码。"""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...


@runtime_checkable
class DshProcessLauncher(Protocol):
    def start(
        self,
        *,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        log_path: Path,
        identity: LinuxIdentity,
    ) -> DshProcessHandle: ...


@runtime_checkable
class DshCommandRunner(Protocol):
    """以目标 Linux 用户身份同步执行一次性 DSH 命令（如插件安装）。"""

    def run_once(
        self,
        *,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        identity: LinuxIdentity,
        timeout_seconds: float,
    ) -> tuple[int, str]: ...


class PopenDshLauncher:
    """用 ``subprocess.Popen`` 启动 DSH Web，并在 root 下切换 uid/gid。"""

    def start(
        self,
        *,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        log_path: Path,
        identity: LinuxIdentity,
    ) -> DshProcessHandle:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        demotion = self._demotion_kwargs(identity)
        log_file = log_path.open("ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                **demotion,
            )
        finally:
            log_file.close()
        logger.info(
            "DSH Web 子进程已启动 pid=%s user=%s uid=%s command=%s",
            process.pid,
            identity.user,
            demotion.get("user") or os.geteuid(),
            command,
        )
        return process

    def run_once(
        self,
        *,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        identity: LinuxIdentity,
        timeout_seconds: float,
    ) -> tuple[int, str]:
        """同步执行一次性命令并等待结束，返回 ``(退出码, 输出尾部)``。

        超时按退出码 124 返回（与 ``timeout(1)`` 约定一致）；输出只保留
        最近 4000 字符，供结构化日志定位失败原因。
        """
        demotion = self._demotion_kwargs(identity)

        def _text(value: object) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value or "")

        try:
            result = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_seconds,
                **demotion,
            )
        except subprocess.TimeoutExpired as exc:
            return 124, (_text(exc.stdout) + _text(exc.stderr))[-4000:]
        output = ((result.stdout or "") + (result.stderr or ""))[-4000:]
        return int(result.returncode), output

    @staticmethod
    def _demotion_kwargs(identity: LinuxIdentity) -> dict[str, object]:
        """需要切换身份时构造 Popen 降权参数；无 root 权限且目标不同则拒绝。"""
        current_uid = os.geteuid()
        if identity.uid == current_uid:
            return {}
        if current_uid != 0:
            raise AccessDenied(
                f"Agent Bridge 需以 root 运行才能以 {identity.user} 身份启动 DSH Web"
                f"（当前 euid={current_uid}）"
            )
        return {"user": identity.user, "group": identity.gid, "extra_groups": [identity.gid]}
