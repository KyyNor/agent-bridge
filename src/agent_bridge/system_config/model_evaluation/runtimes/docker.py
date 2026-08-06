"""通过本机 Docker CLI 启动和管理评估容器。"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path

from .protocol import ContainerHandle, ContainerRuntime, ContainerSpec

logger = logging.getLogger(__name__)


class DockerCliRuntime(ContainerRuntime):
    """不引入 Docker SDK，复用服务现有的子进程生命周期管理方式。"""

    def __init__(self, docker_bin: str | None = None) -> None:
        self._docker_bin = docker_bin or os.environ.get("AGENT_BRIDGE_DOCKER_BIN", "docker")
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    def status(self) -> dict[str, object]:
        try:
            result = subprocess.run(
                [self._docker_bin, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "message": f"Docker 不可用：{exc}"}
        if result.returncode:
            message = (result.stderr or result.stdout).strip() or "无法连接 Docker daemon"
            return {"available": False, "message": f"Docker 不可用：{message}"}
        return {"available": True, "version": result.stdout.strip(), "message": "本地 Docker 运行时已就绪"}

    def image_exists(self, image: str) -> bool:
        try:
            result = subprocess.run(
                [self._docker_bin, "image", "inspect", image],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def run(self, spec: ContainerSpec, *, log_path: Path) -> ContainerHandle:
        spec.work_dir.mkdir(parents=True, exist_ok=True)
        cidfile = spec.work_dir / f"container-{uuid.uuid4().hex}.cid"
        command = [self._docker_bin, "run", "--rm", "--cidfile", str(cidfile)]
        for key, value in spec.labels.items():
            command.extend(["--label", f"{key}={value}"])
        if spec.mount_workspace:
            command.extend(["--mount", f"type=bind,src={spec.work_dir},dst=/workspace"])
        command.extend(["--workdir", spec.container_workdir, "--network", spec.network])
        if spec.read_only:
            command.append("--read-only")
            command.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])
        if spec.cap_drop_all:
            command.extend(["--cap-drop", "ALL"])
        if spec.no_new_privileges:
            command.extend(["--security-opt", "no-new-privileges"])
        if spec.pids_limit is not None:
            command.extend(["--pids-limit", str(spec.pids_limit)])
        if spec.memory:
            command.extend(["--memory", spec.memory])
        if spec.cpus is not None:
            command.extend(["--cpus", str(spec.cpus)])
        for key, value in spec.environment.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend([spec.image, *spec.command])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, text=True)
        finally:
            log_file.close()
        container_id = self._wait_for_cidfile(cidfile, process)
        handle = ContainerHandle(container_id=container_id, image=spec.image, command=spec.command)
        with self._lock:
            self._processes[container_id] = process
        return handle

    def wait(self, handle: ContainerHandle) -> int:
        with self._lock:
            process = self._processes.get(handle.container_id)
        if process is None:
            return 1
        return_code = process.wait()
        with self._lock:
            self._processes.pop(handle.container_id, None)
        return return_code

    def poll(self, handle: ContainerHandle) -> int | None:
        with self._lock:
            process = self._processes.get(handle.container_id)
        return None if process is None else process.poll()

    def exec(self, handle: ContainerHandle, command: str, *, timeout_seconds: int = 120) -> dict[str, object]:
        try:
            result = subprocess.run(
                [self._docker_bin, "exec", handle.container_id, "sh", "-lc", command],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {"return_code": 124, "stdout": exc.stdout or "", "stderr": "命令执行超时"}
        return {"return_code": result.returncode, "stdout": result.stdout[-16000:], "stderr": result.stderr[-16000:]}

    def stop(self, handle: ContainerHandle) -> None:
        subprocess.run([self._docker_bin, "stop", "--time", "5", handle.container_id], capture_output=True, text=True, check=False)
        with self._lock:
            process = self._processes.pop(handle.container_id, None)
        if process and process.poll() is None:
            process.terminate()

    def cleanup_managed(self) -> list[str]:
        try:
            result = subprocess.run(
                [self._docker_bin, "ps", "-aq", "--filter", "label=com.agent-bridge.managed=true"],
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        container_ids = [item for item in result.stdout.splitlines() if item]
        for container_id in container_ids:
            subprocess.run([self._docker_bin, "rm", "-f", container_id], capture_output=True, text=True, check=False)
        return container_ids

    @staticmethod
    def _wait_for_cidfile(cidfile: Path, process: subprocess.Popen[str]) -> str:
        for _ in range(50):
            if cidfile.exists():
                container_id = cidfile.read_text(encoding="utf-8").strip()
                if container_id:
                    return container_id
            if process.poll() is not None:
                break
            threading.Event().wait(0.05)
        return f"docker-client-{process.pid}"
