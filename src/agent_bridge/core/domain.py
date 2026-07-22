from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class AgentBridgeError(Exception):
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AccessDenied(AgentBridgeError):
    status_code = 403


class NotFound(AgentBridgeError):
    status_code = 404


class ValidationError(AgentBridgeError):
    status_code = 400


class ConflictError(AgentBridgeError):
    status_code = 409


class KbRole(str, Enum):
    viewer = "viewer"
    contributor = "contributor"
    admin = "admin"


class DocumentStatus(str, Enum):
    active = "active"
    deleted = "deleted"


class SyncJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class SyncStateStatus(str, Enum):
    not_synced = "not_synced"
    synced = "synced"
    sync_failed = "sync_failed"
    delete_pending = "delete_pending"
    deleted = "deleted"
    delete_failed = "delete_failed"


class Operation(str, Enum):
    create = "create"
    update = "update"
    delete = "delete"
    move = "move"


@dataclass(frozen=True)
class Actor:
    linux_user: str
    is_global_admin: bool


def can_view_kb(role: KbRole | None) -> bool:
    return role in {KbRole.viewer, KbRole.contributor, KbRole.admin}


def can_write_own_doc(role: KbRole | None) -> bool:
    return role in {KbRole.contributor, KbRole.admin}


def can_manage_kb(role: KbRole | None) -> bool:
    return role == KbRole.admin


def require_admin_user(linux_user: str, admins: set[str]) -> None:
    if linux_user not in admins:
        raise AccessDenied("global admin permission required")


@dataclass(frozen=True)
class BackendDocStatus:
    status: str
    chunk_count: int | None = None
    progress: float | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class BackendCapabilities:
    supports_folders: bool
    supports_agents: bool = False
    supports_managed_resources: bool = False


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    content: str
    document_name: str
    similarity: float
    dataset_id: str


@dataclass(frozen=True)
class AskResult:
    answer: str
    chunks: list[RetrievalResult]
    session_id: str | None


@dataclass(frozen=True)
class RetrievalStrategy:
    backend_slug: str
    agent_id: str | None = None


class BackendAdapter(Protocol):
    def create_kb(self, slug: str, name: str) -> str: ...
    def delete_kb(self, backend_kb_id: str) -> None: ...
    def capabilities(self) -> BackendCapabilities: ...
    def upload(
        self,
        backend_kb_id: str,
        doc_slug: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str: ...
    def move(
        self,
        backend_kb_id: str,
        backend_doc_id: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str: ...
    def relocate(
        self,
        backend_kb_id: str,
        backend_doc_id: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str: ...
    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None: ...
    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus: ...
    def retrieve(self, backend_kb_id: str, question: str, top_k: int = 6) -> list[RetrievalResult]: ...
    def ask(self, backend_kb_id: str, question: str, chat_id: str | None = None, session_id: str | None = None, agent_id: str | None = None) -> tuple[AskResult, str]: ...


@runtime_checkable
class AgentManagementBackend(Protocol):
    """可选的后端 Agent 管理能力。

    应用层仅面向该协议，不感知 Weknora 等具体后端类型。
    """

    def list_agents(self) -> list[dict[str, Any]]: ...
    def get_type_presets(self) -> list[dict[str, Any]]: ...
    def create_agent(self, name: str, preset_config: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class ManagedResourcesBackend(Protocol):
    """可选的后端托管资源初始化与自愈能力。"""

    def ensure_managed_resources(self) -> dict[str, Any]: ...
