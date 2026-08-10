"""内部系统的数据归属与访问判定。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent_bridge.core.domain import AccessDenied, NotFound, ValidationError, require_admin_user


_GROUP_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ResourceVisibility(str, Enum):
    group = "group"
    shared = "shared"


@dataclass(frozen=True)
class ResourceScope:
    owner_group_key: str
    visibility: ResourceVisibility

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ResourceScope":
        try:
            visibility = ResourceVisibility(str(record.get("visibility") or "shared"))
        except ValueError as exc:
            raise ValidationError("资源可见范围无效") from exc
        return cls(
            owner_group_key=str(record.get("owner_group_key") or ""),
            visibility=visibility,
        )


class AccessControlService:
    """维护单小组成员关系，并集中执行资源读写策略。"""

    maintenance_group_key = "system-maintainers"

    def __init__(self, repository, admins: set[str]) -> None:
        self.repository = repository
        self.admins = admins

    def bootstrap_admin_memberships(self) -> None:
        if not self.admins:
            return
        self.repository.upsert_group(
            group_key=self.maintenance_group_key,
            name="系统维护组",
            description="Agent Bridge 管理员的默认数据归属组",
            actor=sorted(self.admins)[0],
        )
        for admin in sorted(self.admins):
            if self.repository.get_membership(admin) is None:
                self.repository.set_membership(
                    user_id=admin,
                    group_key=self.maintenance_group_key,
                    actor=admin,
                )

    def upsert_group(
        self, *, actor: str, group_key: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = group_key.strip().lower()
        if not _GROUP_KEY_PATTERN.fullmatch(normalized_key):
            raise ValidationError("小组标识只能包含小写字母、数字、点、下划线和短横线")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValidationError("小组名称不能为空")
        return self.repository.upsert_group(
            group_key=normalized_key,
            name=normalized_name,
            description=description.strip(),
            actor=actor,
        )

    def list_groups(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.repository.list_groups()

    def set_user_group(
        self, *, actor: str, user_id: str, group_key: str
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_user = user_id.strip()
        if not normalized_user:
            raise ValidationError("用户 ID 不能为空")
        if self.repository.get_group(group_key) is None:
            raise NotFound(f"小组不存在：{group_key}")
        return self.repository.set_membership(
            user_id=normalized_user,
            group_key=group_key,
            actor=actor,
        )

    def remove_user_group(self, *, actor: str, user_id: str) -> dict[str, bool]:
        require_admin_user(actor, self.admins)
        return {"deleted": self.repository.delete_membership(user_id)}

    def list_memberships(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return self.repository.list_memberships()

    def actor_context(self, actor: str) -> dict[str, Any]:
        membership = self.repository.get_membership(actor)
        return {
            "user_id": actor,
            "group_key": membership["group_key"] if membership else None,
            "group_name": membership["group_name"] if membership else None,
            "is_maintenance_admin": actor in self.admins,
        }

    def new_resource_scope(
        self, *, actor: str, visibility: ResourceVisibility | str
    ) -> ResourceScope:
        try:
            resolved_visibility = ResourceVisibility(visibility)
        except ValueError as exc:
            raise ValidationError("资源可见范围必须是 group 或 shared") from exc
        membership = self.repository.get_membership(actor)
        if membership is None:
            raise AccessDenied("当前用户尚未分配小组，不能新建资源")
        return ResourceScope(
            owner_group_key=str(membership["group_key"]),
            visibility=resolved_visibility,
        )

    def can_read(self, *, actor: str, scope: ResourceScope) -> bool:
        if actor in self.admins or scope.visibility is ResourceVisibility.shared:
            return True
        membership = self.repository.get_membership(actor)
        return bool(
            membership
            and scope.owner_group_key
            and membership["group_key"] == scope.owner_group_key
        )

    def can_write(self, *, actor: str, scope: ResourceScope) -> bool:
        if actor in self.admins:
            return True
        membership = self.repository.get_membership(actor)
        return bool(
            membership
            and scope.owner_group_key
            and membership["group_key"] == scope.owner_group_key
        )

    def require_read(self, *, actor: str, scope: ResourceScope) -> None:
        if not self.can_read(actor=actor, scope=scope):
            raise AccessDenied("无权访问其他小组的数据")

    def require_write(self, *, actor: str, scope: ResourceScope) -> None:
        if not self.can_write(actor=actor, scope=scope):
            raise AccessDenied("无权修改其他小组的数据")
