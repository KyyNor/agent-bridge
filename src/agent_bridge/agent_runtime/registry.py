from __future__ import annotations

from agent_bridge.agent_runtime.adapters import (
    ClaudeCodingAgent,
    CodexCodingAgent,
    DshCodingAgent,
    OpenCodeCodingAgent,
    PiCodingAgent,
)
from agent_bridge.agent_runtime.types import CodingAgent
from agent_bridge.core.config import AgentBackendConfig, AgentRuntimeConfig, normalize_agent_runtime_config


class UnknownCodingAgentError(ValueError):
    pass


class CodingAgentRegistry:
    def __init__(
        self,
        *,
        default_backend: str = "claude",
        agents: list[CodingAgent] | None = None,
    ) -> None:
        self.default_backend = default_backend
        self._agents: dict[str, CodingAgent] = {}
        for agent in agents or []:
            self.register(agent)

    def register(self, agent: CodingAgent) -> None:
        self._agents[agent.backend_key] = agent

    def get(self, backend_key: str | None = None) -> CodingAgent:
        key = backend_key or self.default_backend
        try:
            return self._agents[key]
        except KeyError as exc:
            raise UnknownCodingAgentError(f"coding agent backend '{key}' is not registered") from exc

    def keys(self) -> list[str]:
        return sorted(self._agents)


def _validate_effort(backend: AgentBackendConfig, agent: CodingAgent) -> None:
    """按实现声明的取值集合校验 effort，避免非法值留到运行期才被 CLI 拒绝。

    ``supported_efforts`` 为 None 的实现（如 OpenCode 的 provider 相关 variant）
    不做枚举校验，取值原样透传；空集合表示该实现完全不支持配置 effort
    （如 DSH 的托管供应商路由不暴露 reasoning effort）。
    """
    if backend.effort is None:
        return
    supported = agent.supported_efforts
    if supported is None:
        return
    if not supported:
        raise ValueError(
            f"Agent 后端 '{backend.slug}' 不支持配置思考力度，请留空以使用其默认值"
        )
    if backend.effort not in supported:
        raise ValueError(
            f"Agent 后端 '{backend.slug}' 不支持 effort 取值 {backend.effort!r}，"
            f"可选值：{', '.join(sorted(supported))}"
        )


def create_coding_agent_registry(
    config: AgentRuntimeConfig | None = None,
    *,
    dsh_runtime: object | None = None,
) -> CodingAgentRegistry:
    runtime_config = normalize_agent_runtime_config(config or AgentRuntimeConfig())
    registry = CodingAgentRegistry(default_backend=runtime_config.default_backend)

    # Claude is always registered because some existing runtimes are explicitly
    # Claude-only even if general agent runs later choose a different default.
    registry.register(ClaudeCodingAgent())

    for backend in runtime_config.backends:
        if backend.agent_type == "claude":
            agent: CodingAgent = ClaudeCodingAgent(
                backend_key=backend.slug,
                model=backend.model,
                effort=backend.effort,
            )
        elif backend.agent_type == "opencode":
            agent = OpenCodeCodingAgent(
                backend_key=backend.slug,
                command=backend.command or "opencode",
                model=backend.model,
                variant=backend.effort,
            )
        elif backend.agent_type == "codex":
            agent = CodexCodingAgent(
                backend_key=backend.slug,
                command=backend.command or "codex",
                model=backend.model,
                effort=backend.effort,
            )
        elif backend.agent_type == "pi":
            agent = PiCodingAgent(
                backend_key=backend.slug,
                command=backend.command or "pi",
                model=backend.model,
                thinking=backend.effort,
            )
        elif backend.agent_type == "dsh":
            agent = DshCodingAgent(
                backend_key=backend.slug,
                command=backend.command or "dsh",
                model=backend.model,
                runtime=dsh_runtime,
            )
        else:
            raise UnknownCodingAgentError(
                f"coding agent backend '{backend.slug}' has unsupported type '{backend.agent_type}'"
            )
        _validate_effort(backend, agent)
        registry.register(agent)
    return registry
