def test_page_aligned_package_layout_imports() -> None:
    from agent_bridge.agent_runtime.service import AgentService
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
    from agent_bridge.capability_hub.profiles.docs import install_profile_to_cwd
    from agent_bridge.capability_hub.sources.mcp.http_client import McpHttpClient
    from agent_bridge.knowledge_management.code_knowledge.service import CodeGraphService
    from agent_bridge.knowledge_management.docs_knowledge.backends.mock import MockBackend
    from agent_bridge.system_config.scripts.service import ScriptService
    from agent_bridge.system_config.skills.service import SkillService

    assert AgentService is not None
    assert AgentBridgeService is not None
    assert create_mcp_server is not None
    assert install_profile_to_cwd is not None
    assert McpHttpClient is not None
    assert CodeGraphService is not None
    assert MockBackend is not None
    assert ScriptService is not None
    assert SkillService is not None
