"""MetaMCP 顶层工具的稳定目录与映射。

这里的工具会直接出现在 ``/mcp`` 的 tools/list 响应中。``search`` 与
``execute`` 是网关固定入口，不属于可关闭范围；动态 pin 工具仍由所属
Capability Profile 管理。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_bridge.capability_hub.sources.builtin.wiki import WIKI_SEARCH_ENABLED


@dataclass(frozen=True)
class TopLevelMcpTool:
    name: str
    title: str
    description: str
    kind: str
    service_key: str | None = None
    tool_name: str | None = None


DIRECT_BUILTIN_TOOLS: tuple[TopLevelMcpTool, ...] = (
    TopLevelMcpTool("wiki_ask", "知识库问答", "基于当前 Profile 可访问的知识库生成回答。", "direct_builtin", "wiki", "ask"),
    TopLevelMcpTool("codegraph_explore", "代码图谱探索", "检索当前 Profile 可访问代码仓库的代码图谱。", "direct_builtin", "codegraph", "codegraph_explore"),
    TopLevelMcpTool("memory_search", "记忆检索", "搜索当前 Profile 绑定的记忆区块。", "direct_builtin", "memory", "search"),
    TopLevelMcpTool("memory_timeline", "记忆时间线", "按时间浏览当前 Profile 绑定的记忆区块。", "direct_builtin", "memory", "timeline"),
    TopLevelMcpTool("memory_get", "读取记忆", "读取当前 Profile 绑定记忆区块中的完整记录。", "direct_builtin", "memory", "get"),
    TopLevelMcpTool("query_business_ledger", "查询业务台账", "受控查询当前 Profile 可访问的业务台账。", "direct_builtin", "business_ledger", "query"),
)

ARTIFACTS_SEARCH_TOOL = TopLevelMcpTool(
    "artifacts_search", "工作流产物检索", "搜索当前 Profile 的工作流产物。", "artifacts"
)

WORKFLOW_TOOLS: tuple[TopLevelMcpTool, ...] = (
    TopLevelMcpTool("workflow_get_task", "领取工作流任务", "仅在工作流运行上下文中提供。", "workflow"),
    TopLevelMcpTool("workflow_set_task", "写入工作流任务", "仅在工作流运行上下文中提供。", "workflow"),
    TopLevelMcpTool("workflow_run_log", "追加工作流日志", "仅在工作流运行上下文中提供。", "workflow"),
)


def top_level_mcp_tools() -> tuple[TopLevelMcpTool, ...]:
    """返回当前版本实际支持配置的顶层工具（不含 search / execute）。"""

    direct = DIRECT_BUILTIN_TOOLS
    if WIKI_SEARCH_ENABLED:
        direct = (
            TopLevelMcpTool("wiki_search", "知识库检索", "检索当前 Profile 可访问的知识库原始片段。", "direct_builtin", "wiki", "search"),
            *direct,
        )
    return (ARTIFACTS_SEARCH_TOOL, *direct, *WORKFLOW_TOOLS)


def top_level_tool_for_capability(service_key: str, tool_name: str) -> str | None:
    for spec in top_level_mcp_tools():
        if spec.service_key == service_key and spec.tool_name == tool_name:
            return spec.name
    return None


def top_level_tool_by_name(name: str) -> TopLevelMcpTool | None:
    return next((spec for spec in top_level_mcp_tools() if spec.name == name), None)
