"""聚合 ``SQLiteStore`` 的显式兼容 mixin。"""

from __future__ import annotations

from agent_bridge.storage.facades.capabilities import CapabilitiesFacadeMixin
from agent_bridge.storage.facades.core import CoreFacadeMixin
from agent_bridge.storage.facades.governance import GovernanceFacadeMixin
from agent_bridge.storage.facades.knowledge import KnowledgeFacadeMixin
from agent_bridge.storage.facades.workflows import WorkflowsFacadeMixin


class SQLiteStoreFacade(
    CoreFacadeMixin,
    WorkflowsFacadeMixin,
    CapabilitiesFacadeMixin,
    GovernanceFacadeMixin,
    KnowledgeFacadeMixin,
):
    """由明确领域 mixin 组成的兼容接口。"""
