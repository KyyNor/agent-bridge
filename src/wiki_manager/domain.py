from __future__ import annotations

from enum import Enum


class KbRole(str, Enum):
    viewer = "viewer"
    contributor = "contributor"
    admin = "admin"


class SyncJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
