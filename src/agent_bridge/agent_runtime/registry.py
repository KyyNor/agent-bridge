from __future__ import annotations

from agent_bridge.agent_runtime.adapters import ClaudeCodingAgent
from agent_bridge.agent_runtime.types import CodingAgent
from agent_bridge.core.config import AgentRuntimeConfig


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


def create_coding_agent_registry(config: AgentRuntimeConfig | None = None) -> CodingAgentRegistry:
    runtime_config = config or AgentRuntimeConfig()
    registry = CodingAgentRegistry(default_backend=runtime_config.default_backend)

    # Claude is always registered because some existing runtimes are explicitly
    # Claude-only even if general agent runs later choose a different default.
    registry.register(ClaudeCodingAgent())

    for backend in runtime_config.backends:
        if backend.agent_type == "claude":
            registry.register(
                ClaudeCodingAgent(
                    backend_key=backend.slug,
                    model=backend.model,
                )
            )
            continue
        raise UnknownCodingAgentError(
            f"coding agent backend '{backend.slug}' has unsupported type '{backend.agent_type}'"
        )
    return registry
