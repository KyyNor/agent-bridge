from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class SkillDefinition:
    skill_name: str
    name: str
    description: str
    default_path: Path


class SkillService:
    def __init__(self, *, store: SQLiteStore, admins: set[str]) -> None:
        self.store = store
        self.admins = admins
        defaults = Path(__file__).parent / "defaults"
        self._definitions = {
            "design_workflow": SkillDefinition(
                skill_name="design_workflow",
                name="Design Workflow",
                description="编写 Agent Bridge workflow.js 与工作流结构定义的提示词。",
                default_path=defaults / "design_workflow.md",
            )
        }

    def list_skills(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        overrides = {item["skill_name"]: item for item in self.store.list_skill_prompt_overrides()}
        return [
            self._payload(definition, overrides.get(skill_name), include_prompt=False)
            for skill_name, definition in sorted(self._definitions.items())
        ]

    def get_skill(self, actor: str, skill_name: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        definition = self._definition(skill_name)
        override = self.store.get_skill_prompt_override(skill_name)
        return self._payload(definition, override, include_prompt=True)

    def save_skill(self, actor: str, skill_name: str, prompt: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        definition = self._definition(skill_name)
        normalized = prompt.strip()
        if not normalized:
            raise ValidationError("prompt is required")
        override = self.store.upsert_skill_prompt_override(
            skill_name=definition.skill_name,
            prompt=normalized,
            updated_by=actor,
        )
        return self._payload(definition, override, include_prompt=True)

    def reset_skill(self, actor: str, skill_name: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        definition = self._definition(skill_name)
        self.store.delete_skill_prompt_override(definition.skill_name)
        return self._payload(definition, None, include_prompt=True)

    def load_skill(self, actor: str, skill_name: str) -> dict[str, Any]:
        return self.get_skill(actor, skill_name)

    def _definition(self, skill_name: str) -> SkillDefinition:
        normalized = skill_name.strip()
        definition = self._definitions.get(normalized)
        if definition is None:
            raise NotFound("skill not found")
        return definition

    def _default_prompt(self, definition: SkillDefinition) -> str:
        try:
            return definition.default_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise NotFound("default skill prompt not found") from exc

    def _payload(
        self,
        definition: SkillDefinition,
        override: dict[str, Any] | None,
        *,
        include_prompt: bool,
    ) -> dict[str, Any]:
        default_prompt = self._default_prompt(definition)
        prompt = str(override["prompt"]) if override is not None else default_prompt
        payload: dict[str, Any] = {
            "skill_name": definition.skill_name,
            "name": definition.name,
            "description": definition.description,
            "source": "database" if override is not None else "default",
            "updated_at": override.get("updated_at") if override is not None else None,
            "updated_by": override.get("updated_by") if override is not None else None,
        }
        if include_prompt:
            payload["prompt"] = prompt
            payload["default_prompt"] = default_prompt
        else:
            payload["prompt_preview"] = prompt[:160]
        return payload
