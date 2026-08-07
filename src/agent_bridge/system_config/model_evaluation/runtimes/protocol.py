"""Docker 运行时抽象，供不同评估 runner 共用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class ContainerSpec:
    image: str
    command: tuple[str, ...]
    work_dir: Path
    labels: Mapping[str, str]
    environment: Mapping[str, str] = field(default_factory=dict)
    network: str = "bridge"
    read_only: bool = False
    cap_drop_all: bool = False
    no_new_privileges: bool = False
    pids_limit: int | None = None
    memory: str | None = None
    cpus: float | None = None
    mount_workspace: bool = True
    container_workdir: str = "/workspace"


@dataclass(frozen=True)
class ContainerHandle:
    container_id: str
    image: str
    command: tuple[str, ...]


class ContainerRuntime(Protocol):
    def status(self) -> dict[str, object]: ...

    def image_exists(self, image: str) -> bool: ...

    def run(self, spec: ContainerSpec, *, log_path: Path) -> ContainerHandle: ...

    def wait(self, handle: ContainerHandle) -> int: ...

    def poll(self, handle: ContainerHandle) -> int | None: ...

    def exec(self, handle: ContainerHandle, command: str, *, timeout_seconds: int = 120) -> dict[str, object]: ...

    def stop(self, handle: ContainerHandle) -> None: ...

    def cleanup_managed(self) -> list[str]: ...
