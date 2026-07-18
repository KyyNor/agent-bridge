from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_bridge.core.diff import text_diff
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
            "design_script": SkillDefinition(
                skill_name="design_script",
                name="Design Script",
                description="编写 Agent Bridge 受控脚本的提示词。",
                default_path=defaults / "design_script.md",
            ),
            "design_workflow": SkillDefinition(
                skill_name="design_workflow",
                name="Design Workflow",
                description="设计 Agent Bridge 结构化 DAG 工作流的提示词。",
                default_path=defaults / "design_workflow.md",
            ),
            "design_html_report": SkillDefinition(
                skill_name="design_html_report",
                name="Design HTML Report",
                description="为 workflow 总结类产物生成面向人类阅读的 HTML 报告。",
                default_path=defaults / "design_html_report.md",
            ),
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
        previous_override = self.store.get_skill_prompt_override(definition.skill_name)
        previous_revision_no = (
            int(previous_override.get("current_revision_no") or 0) if previous_override else 0
        )
        previous_hash = None
        if previous_override:
            prev_rev = self.store.get_skill_prompt_revision(definition.skill_name, previous_revision_no)
            if prev_rev:
                previous_hash = prev_rev.get("content_hash")
        override = self.store.upsert_skill_prompt_override(
            skill_name=definition.skill_name,
            prompt=normalized,
            updated_by=actor,
        )
        new_hash = self._skill_content_hash(normalized)
        payload = self._payload(definition, override, include_prompt=True)
        if previous_hash != new_hash:
            revision = self.store.create_skill_prompt_revision(
                skill_name=definition.skill_name,
                content_hash=new_hash,
                snapshot={"prompt": normalized, "source": "database"},
                actor=actor,
            )
            payload["revision_no"] = revision["revision_no"]
        else:
            payload["revision_no"] = previous_revision_no
        return payload

    def reset_skill(self, actor: str, skill_name: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        definition = self._definition(skill_name)
        default_prompt = self._default_prompt(definition)
        self.store.delete_skill_prompt_override(definition.skill_name)
        new_hash = self._skill_content_hash(default_prompt)
        revision = self.store.create_skill_prompt_revision(
            skill_name=definition.skill_name,
            content_hash=new_hash,
            snapshot={"prompt": default_prompt, "source": "default"},
            actor=actor,
        )
        payload = self._payload(definition, None, include_prompt=True)
        payload["revision_no"] = revision["revision_no"]
        return payload

    # --- versioning & diff -----------------------------------------------

    def list_revisions(self, actor: str, skill_name: str, *, limit: int = 100) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        self._definition(skill_name)
        revisions = self.store.list_skill_prompt_revisions(skill_name, limit=limit)
        override = self.store.get_skill_prompt_override(skill_name)
        current = int(override.get("current_revision_no") or 0) if override else 0
        for rev in revisions:
            rev["is_current"] = rev.get("revision_no") == current
        return revisions

    def get_revision(self, actor: str, skill_name: str, revision_no: int) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._definition(skill_name)
        revision = self.store.get_skill_prompt_revision(skill_name, revision_no)
        if revision is None:
            raise NotFound("skill revision not found")
        override = self.store.get_skill_prompt_override(skill_name)
        current = int(override.get("current_revision_no") or 0) if override else 0
        revision["is_current"] = revision.get("revision_no") == current
        return revision

    def diff_revisions(
        self, actor: str, skill_name: str, *, from_no: int | None, to_no: int | None
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._definition(skill_name)
        revisions = self.store.list_skill_prompt_revisions(skill_name, limit=500)
        if not revisions:
            raise NotFound("skill has no revisions")
        current = 0
        for rev in revisions:
            if rev.get("is_current"):
                current = rev["revision_no"]
        if current == 0 and revisions:
            current = revisions[0]["revision_no"]
        to_revision_no = to_no if to_no is not None else current
        from_revision_no = from_no if from_no is not None else max(to_revision_no - 1, 1)
        to_rev = self.store.get_skill_prompt_revision(skill_name, to_revision_no)
        from_rev = self.store.get_skill_prompt_revision(skill_name, from_revision_no)
        if to_rev is None:
            raise NotFound(f"skill revision {to_revision_no} not found")
        if from_rev is None:
            raise NotFound(f"skill revision {from_revision_no} not found")
        from_text = str((from_rev.get("snapshot") or {}).get("prompt") or "")
        to_text = str((to_rev.get("snapshot") or {}).get("prompt") or "")
        return {
            "entity_type": "skill",
            "entity_key": skill_name,
            "from_revision": from_revision_no,
            "to_revision": to_revision_no,
            "text": text_diff(
                from_text,
                to_text,
                from_label=f"revision {from_revision_no}",
                to_label=f"revision {to_revision_no}",
            ),
        }

    @staticmethod
    def _skill_content_hash(prompt: str) -> str:
        return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()

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
