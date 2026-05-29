from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WikiManagerError(Exception):
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AccessDenied(WikiManagerError):
    status_code = 403


class NotFound(WikiManagerError):
    status_code = 404


class ValidationError(WikiManagerError):
    status_code = 400


class ConflictError(WikiManagerError):
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
