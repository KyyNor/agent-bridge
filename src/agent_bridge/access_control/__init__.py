"""Agent Bridge 调用身份与数据访问边界。"""

from agent_bridge.access_control.identity import ActorIdentity, IdentityConfig, RequestIdentityResolver

__all__ = ["ActorIdentity", "IdentityConfig", "RequestIdentityResolver"]
from agent_bridge.access_control.service import (
    AccessControlService,
    ResourceScope,
    ResourceVisibility,
)
from agent_bridge.access_control.resources import SHAREABLE_RESOURCE_TYPES, ScopedResourceType

__all__ = [
    "AccessControlService",
    "ResourceScope",
    "ResourceVisibility",
    "ScopedResourceType",
    "SHAREABLE_RESOURCE_TYPES",
]
