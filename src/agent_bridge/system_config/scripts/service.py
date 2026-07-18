from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from agent_bridge.automation.workflows.validation import WORKFLOW_VALIDATION_INPUT_SCHEMA
from agent_bridge.core.config import AgentBridgePaths, load_server_config
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.core.diff import text_diff
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.system_config.scripts.runtime_support import render_runner, render_runtime_helper
from agent_bridge.system_config.scripts.syntax import check_python_syntax


SCRIPT_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
OWNER_TYPES = {"workflow", "skill", "profile", "system"}
SCRIPT_STATUSES = {"active", "disabled"}
RUN_TYPES = {"test", "mcp"}
MAX_CAPTURE_CHARS = 256_000
DEFAULT_SCRIPT_ACTOR = "__agent_bridge_default__"


@dataclass(frozen=True)
class ScriptProcessResult:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool = False


@dataclass(frozen=True)
class BuiltInScriptDefinition:
    script_key: str
    name: str
    description: str
    default_path: Path
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ScriptService:
    def __init__(self, *, paths: AgentBridgePaths, store: SQLiteStore, admins: set[str]) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.base_run_dir = paths.run_dir / "scripts"
        defaults = Path(__file__).parent / "defaults"
        self._builtins = {
            "system.validate_workflow": BuiltInScriptDefinition(
                script_key="system.validate_workflow",
                name="Validate Workflow",
                description="Validate an Agent Bridge workflow definition.",
                default_path=defaults / "system_validate_workflow.py",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "workflow": {
                            **WORKFLOW_VALIDATION_INPUT_SCHEMA,
                        },
                    },
                    "required": ["workflow"],
                },
                output_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "valid": {"type": "boolean"},
                        "errors": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                        "warnings": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                    },
                    "required": ["valid", "errors", "warnings"],
                },
            )
        }

    def list_scripts(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        scripts = {script["script_key"]: self._with_script_source(script) for script in self.store.scripts.list_scripts()}
        for script_key, definition in self._builtins.items():
            scripts.setdefault(script_key, self._default_script(definition))
        return [self._script_payload(scripts[key], include_code=False) for key in sorted(scripts)]

    def get_script(self, actor: str, script_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return self._script_payload(self._require_script(script_key), include_code=True)

    def upsert_script(
        self,
        *,
        actor: str,
        script_key: str,
        name: str,
        description: str,
        language: str,
        code: str,
        status: str,
        owner_type: str,
        owner_key: str,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = self._validate_script_key(script_key)
        builtin = self._builtins.get(normalized_key)
        normalized_language = language.strip().lower()
        if normalized_language != "python":
            raise ValidationError("only python scripts are supported")
        normalized_status = self._validate_status(status)
        normalized_owner_type, normalized_owner_key = self._validate_owner(owner_type, owner_key)
        if builtin is not None:
            if normalized_status != "active":
                raise ValidationError("cannot disable built-in script")
            if normalized_owner_type != "system" or normalized_owner_key:
                raise ValidationError("cannot change built-in script owner")
        normalized_code = code.rstrip() + "\n"
        if not normalized_code.strip():
            raise ValidationError("code is required")
        normalized_name = builtin.name if builtin is not None else name.strip() or normalized_key
        if input_schema is None:
            raise ValidationError("input_schema is required")
        normalized_input_schema = self._validate_schema("input_schema", input_schema, require_object_root=True)
        normalized_output_schema = None
        if output_schema is not None:
            normalized_output_schema = self._validate_schema("output_schema", output_schema)
        if builtin is not None:
            if normalized_input_schema != builtin.input_schema:
                raise ValidationError("cannot change built-in script input_schema")
            if normalized_output_schema != builtin.output_schema:
                raise ValidationError("cannot change built-in script output_schema")
        new_content_hash = self._content_hash(
            normalized_code,
            language=normalized_language,
            input_schema=normalized_input_schema,
            output_schema=normalized_output_schema,
        )
        with self.store.transaction():
            previous_revisions = self.store.scripts.list_revisions(normalized_key, limit=1)
            is_first_revision = not previous_revisions
            previous_hash = previous_revisions[0]["content_hash"] if previous_revisions else None
            content_changed = is_first_revision or previous_hash != new_content_hash
            script = self.store.scripts.upsert_script(
                script_key=normalized_key,
                name=normalized_name,
                description=builtin.description if builtin is not None else description.strip(),
                language=normalized_language,
                code=normalized_code,
                input_schema=normalized_input_schema,
                output_schema=normalized_output_schema,
                status=normalized_status,
                owner_type=normalized_owner_type,
                owner_key=normalized_owner_key,
                content_hash=new_content_hash,
                actor=actor,
            )
            # Archive a revision whenever execution semantics changed (or on
            # the first save, including an upgraded legacy database).
            revision_no = self.store.scripts.get_current_revision_no(normalized_key)
            if content_changed:
                revision = self.store.scripts.create_revision(
                    script_key=normalized_key,
                    content_hash=new_content_hash,
                    snapshot=self._script_revision_snapshot(script),
                    actor=actor,
                )
                revision_no = revision["revision_no"]
        script["revision_no"] = revision_no
        script["syntax_check"] = check_python_syntax(normalized_code)
        return self._script_payload(script, include_code=True)

    def delete_script(self, actor: str, script_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = self._validate_script_key(script_key)
        if normalized_key in self._builtins:
            raise ValidationError("cannot delete built-in script")
        if self.store.scripts.has_script_runs(normalized_key):
            raise ValidationError("脚本已有运行历史，请改为 disabled，不能删除")
        deleted = self.store.scripts.delete_script(normalized_key)
        if not deleted:
            raise NotFound("script not found")
        return {"script_key": script_key, "deleted": True}

    def reset_script(self, actor: str, script_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = self._validate_script_key(script_key)
        definition = self._builtins.get(normalized_key)
        if definition is None:
            raise NotFound("built-in script not found")
        default = self._default_script(definition)
        with self.store.transaction():
            stored = self._materialize_default_script(definition)
            revisions = self.store.scripts.list_revisions(normalized_key, limit=1)
            revision_no = revisions[0]["revision_no"] if revisions else 0
            if not revisions or revisions[0]["content_hash"] != default["content_hash"]:
                revision = self.store.scripts.create_revision(
                    script_key=normalized_key,
                    content_hash=default["content_hash"],
                    snapshot=self._script_revision_snapshot(stored),
                    actor=actor,
                )
                revision_no = revision["revision_no"]
        stored["revision_no"] = revision_no
        return self._script_payload(stored, include_code=True)

    def test_script(
        self,
        *,
        actor: str,
        script_key: str,
        script_params: dict[str, Any] | None,
        timeout_seconds: int | None,
        profile_key: str | None = None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return self.run_script(
            actor=actor,
            script_key=script_key,
            script_params=script_params or {},
            timeout_seconds=timeout_seconds,
            profile_key=profile_key,
            workflow_context=workflow_context,
            run_type="test",
        )

    def run_script(
        self,
        *,
        actor: str,
        script_key: str,
        script_params: dict[str, Any],
        timeout_seconds: int | None,
        profile_key: str | None,
        workflow_context: dict[str, Any] | None,
        run_type: str = "mcp",
    ) -> dict[str, Any]:
        if run_type not in RUN_TYPES:
            raise ValidationError("invalid script run type")
        script = self._require_script(self._validate_script_key(script_key))
        if script["status"] != "active":
            raise ValidationError("script is disabled")
        if script["language"] != "python":
            raise ValidationError("only python scripts are supported")
        if not isinstance(script_params, dict):
            raise ValidationError("script_params must be an object")
        self._validate_script_params(script["script_key"], script["input_schema"], script_params)
        # Fast-fail on syntax errors before spawning a subprocess.
        syntax = check_python_syntax(str(script.get("code") or ""))
        if not syntax["ok"]:
            first = syntax["errors"][0]
            location = f"line {first['line']}" if first.get("line") else "unknown line"
            raise ValidationError(f"script has syntax errors ({location}): {first.get('msg')}")
        if script.get("source") == "default":
            script = self._materialize_default_script(self._builtins[script["script_key"]])
        timeout = self._timeout(timeout_seconds)
        run_id = f"script_run_{uuid4().hex}"
        envelope = {
            "run_id": run_id,
            "run_type": run_type,
            "script_key": script["script_key"],
            "script_params": script_params,
            "profile_key": profile_key,
            "workflow": {
                "enabled": bool(workflow_context and workflow_context.get("workflow")),
                "workflow_key": (workflow_context or {}).get("workflow_key"),
                "run_id": (workflow_context or {}).get("run_id"),
            },
        }
        process = self._run_python(script, run_id=run_id, envelope=envelope, timeout_seconds=timeout, actor=actor)
        status = "success"
        error_message: str | None = None
        result: dict[str, Any] = {}
        if process.timed_out:
            status = "failed"
            error_message = f"script timed out after {timeout} seconds"
        elif process.exit_code != 0:
            status = "failed"
            stderr_text = process.stderr.strip()
            stdout_text = process.stdout.strip()
            error_message = stderr_text or stdout_text or f"script exited with code {process.exit_code}"
        else:
            result_path = self.base_run_dir / run_id / "result.json"
            if not result_path.is_file():
                status = "failed"
                error_message = "script main(envelope) did not produce result.json"
            else:
                try:
                    parsed = json.loads(result_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    status = "failed"
                    error_message = f"script result file must be a JSON object: {exc}"
                else:
                    if not isinstance(parsed, dict):
                        status = "failed"
                        error_message = "script main(envelope) must return a JSON object"
                    else:
                        result = parsed
                        validation_error = self._validate_script_output(
                            script["script_key"],
                            script.get("output_schema"),
                            result,
                        )
                        if validation_error is not None:
                            status = "failed"
                            error_message = validation_error
        run_params = {"script_params": script_params, "timeout_seconds": timeout, "profile_key": profile_key}
        run = self.store.scripts.create_script_run(
            run_id=run_id,
            script_key=script["script_key"],
            run_type=run_type,
            params=run_params,
            result=result,
            stdout=process.stdout,
            stderr=process.stderr,
            status=status,
            exit_code=process.exit_code,
            error_message=error_message,
            duration_ms=process.duration_ms,
            actor=actor,
        )
        payload = self._run_payload(run, include_logs=True)
        if status != "success":
            raise ValidationError(f"script run failed: {error_message}")
        return payload

    def list_runs(self, actor: str, script_key: str, *, limit: int = 20) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        self._require_script(script_key)
        bounded = min(max(limit, 1), 50)
        return {"runs": [self._run_payload(run, include_logs=False) for run in self.store.scripts.list_script_runs(script_key, limit=bounded)]}

    def get_run(self, actor: str, run_id: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        run = self.store.scripts.get_script_run(run_id)
        if run is None:
            raise NotFound("script run not found")
        return self._run_payload(run, include_logs=True)

    # --- versioning & diff -----------------------------------------------

    def list_revisions(self, actor: str, script_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        normalized_key = self._validate_script_key(script_key)
        self._require_script(normalized_key)
        revisions = self.store.scripts.list_revisions(normalized_key, limit=limit)
        current = self.store.scripts.get_current_revision_no(normalized_key)
        for rev in revisions:
            rev["is_current"] = rev.get("revision_no") == current
        return revisions

    def get_revision(self, actor: str, script_key: str, revision_no: int) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = self._validate_script_key(script_key)
        revision = self.store.scripts.get_revision(normalized_key, revision_no)
        if revision is None:
            raise NotFound("script revision not found")
        current = self.store.scripts.get_current_revision_no(normalized_key)
        revision["is_current"] = revision.get("revision_no") == current
        return revision

    def diff_revisions(
        self, actor: str, script_key: str, *, from_no: int | None, to_no: int | None
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = self._validate_script_key(script_key)
        current = self.store.scripts.get_current_revision_no(normalized_key)
        if current == 0:
            raise NotFound("script has no revisions")
        to_revision_no = to_no if to_no is not None else current
        from_revision_no = from_no if from_no is not None else max(to_revision_no - 1, 1)
        to_rev = self.store.scripts.get_revision(normalized_key, to_revision_no)
        from_rev = self.store.scripts.get_revision(normalized_key, from_revision_no)
        if to_rev is None:
            raise NotFound(f"script revision {to_revision_no} not found")
        if from_rev is None:
            raise NotFound(f"script revision {from_revision_no} not found")
        from_text = str((from_rev.get("snapshot") or {}).get("code") or "")
        to_text = str((to_rev.get("snapshot") or {}).get("code") or "")
        return {
            "entity_type": "script",
            "entity_key": normalized_key,
            "from_revision": from_revision_no,
            "to_revision": to_revision_no,
            "text": text_diff(
                from_text,
                to_text,
                from_label=f"revision {from_revision_no}",
                to_label=f"revision {to_revision_no}",
            ),
        }

    def validate_code(self, actor: str, code: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        return check_python_syntax(code)

    def _run_python(
        self,
        script: dict[str, Any],
        *,
        run_id: str,
        envelope: dict[str, Any],
        timeout_seconds: int,
        actor: str,
    ) -> ScriptProcessResult:
        run_dir = self.base_run_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        script_path = run_dir / "script.py"
        script_path.write_text(str(script["code"]), encoding="utf-8")
        (run_dir / "envelope.json").write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        (run_dir / "script_runner.py").write_text(render_runner(), encoding="utf-8")
        (run_dir / "agent_bridge_runtime.py").write_text(render_runtime_helper(), encoding="utf-8")
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [sys.executable, str(run_dir / "script_runner.py")],
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                cwd=run_dir,
                env=self._runtime_env(actor, envelope),
                check=False,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            return ScriptProcessResult(
                stdout=self._bounded(completed.stdout),
                stderr=self._bounded(completed.stderr),
                exit_code=completed.returncode,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return ScriptProcessResult(
                stdout=self._bounded(exc.stdout or ""),
                stderr=self._bounded(exc.stderr or ""),
                exit_code=None,
                duration_ms=duration_ms,
                timed_out=True,
            )

    def _require_script(self, script_key: str) -> dict[str, Any]:
        normalized_key = self._validate_script_key(script_key)
        script = self.store.scripts.get_script(normalized_key)
        if script is not None:
            return self._with_script_source(script)
        definition = self._builtins.get(normalized_key)
        if definition is None:
            raise NotFound("script not found")
        return self._default_script(definition)

    def _with_script_source(self, script: dict[str, Any]) -> dict[str, Any]:
        payload = dict(script)
        definition = self._builtins.get(str(payload.get("script_key") or ""))
        if definition is None:
            payload["source"] = "database"
            return payload
        if payload.get("updated_by") == DEFAULT_SCRIPT_ACTOR:
            if not self._is_materialized_default(payload, definition):
                return self._materialize_default_script(definition)
            payload["source"] = "default"
            return payload
        payload["source"] = "database"
        return payload

    def _materialize_default_script(self, definition: BuiltInScriptDefinition) -> dict[str, Any]:
        default = self._default_script(definition)
        stored = self.store.scripts.upsert_script(
            script_key=default["script_key"],
            name=default["name"],
            description=default["description"],
            language=default["language"],
            code=default["code"],
            input_schema=default["input_schema"],
            output_schema=default["output_schema"],
            status=default["status"],
            owner_type=default["owner_type"],
            owner_key=default["owner_key"],
            content_hash=default["content_hash"],
            actor=DEFAULT_SCRIPT_ACTOR,
        )
        return self._with_script_source(stored)

    def _is_materialized_default(self, script: dict[str, Any], definition: BuiltInScriptDefinition) -> bool:
        default = self._default_script(definition)
        return (
            script.get("updated_by") == DEFAULT_SCRIPT_ACTOR
            and script.get("language") == default["language"]
            and script.get("code") == default["code"]
            and script.get("status") == default["status"]
            and script.get("owner_type") == default["owner_type"]
            and script.get("owner_key") == default["owner_key"]
            and script.get("content_hash") == default["content_hash"]
            and script.get("input_schema") == default["input_schema"]
            and script.get("output_schema") == default["output_schema"]
        )

    def _default_script(self, definition: BuiltInScriptDefinition) -> dict[str, Any]:
        try:
            code = definition.default_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise NotFound("default script not found") from exc
        return {
            "script_key": definition.script_key,
            "name": definition.name,
            "description": definition.description,
            "language": "python",
            "code": code.rstrip() + "\n",
            "status": "active",
            "owner_type": "system",
            "owner_key": "",
            "content_hash": self._content_hash(
                code.rstrip() + "\n",
                language="python",
                input_schema=definition.input_schema,
                output_schema=definition.output_schema,
            ),
            "input_schema": definition.input_schema,
            "output_schema": definition.output_schema,
            "source": "default",
            "created_by": None,
            "updated_by": None,
            "created_at": None,
            "updated_at": None,
        }

    def _validate_script_key(self, script_key: str) -> str:
        normalized = script_key.strip()
        if not normalized or not SCRIPT_KEY_RE.fullmatch(normalized):
            raise ValidationError("script_key may contain only letters, numbers, dot, hyphen, and underscore")
        return normalized

    def _validate_status(self, status: str) -> str:
        normalized = status.strip() or "active"
        if normalized not in SCRIPT_STATUSES:
            raise ValidationError("invalid script status")
        return normalized

    def _validate_owner(self, owner_type: str, owner_key: str) -> tuple[str, str]:
        normalized_type = owner_type.strip() or "system"
        normalized_key = owner_key.strip()
        if normalized_type not in OWNER_TYPES:
            raise ValidationError("invalid script owner_type")
        if normalized_type == "system":
            return normalized_type, ""
        if not normalized_key:
            raise ValidationError("owner_key is required")
        if normalized_type == "workflow" and self.store.get_workflow_definition(normalized_key) is None:
            raise ValidationError("workflow owner not found")
        if normalized_type == "profile" and self.store.get_project_profile(normalized_key) is None:
            raise ValidationError("profile owner not found")
        if normalized_type == "skill":
            # Skill definitions may be partly built-in and partly database-backed.
            # Keep this binding lightweight until skill package management lands.
            return normalized_type, normalized_key
        return normalized_type, normalized_key

    def _timeout(self, timeout_seconds: int | None) -> int:
        if timeout_seconds is None:
            return 60
        timeout = int(timeout_seconds)
        if timeout < 1 or timeout > 600:
            raise ValidationError("timeout_seconds must be between 1 and 600")
        return timeout

    def _script_payload(self, script: dict[str, Any], *, include_code: bool) -> dict[str, Any]:
        payload = dict(script)
        payload.setdefault("source", "database")
        payload["is_builtin"] = str(payload.get("script_key") or "") in self._builtins
        if "revision_no" not in payload:
            payload["revision_no"] = int(payload.get("current_revision_no") or 0)
        payload.pop("current_revision_no", None)
        if not include_code:
            payload.pop("code", None)
            payload["code_preview"] = str(script.get("code") or "")[:160]
        return payload

    def _script_revision_snapshot(self, script: dict[str, Any]) -> dict[str, Any]:
        """Capture the fields needed to reconstruct/diff a script version."""
        return {
            "script_key": script.get("script_key"),
            "name": script.get("name"),
            "description": script.get("description"),
            "language": script.get("language"),
            "code": script.get("code"),
            "status": script.get("status"),
            "owner_type": script.get("owner_type"),
            "owner_key": script.get("owner_key"),
            "input_schema": script.get("input_schema"),
            "output_schema": script.get("output_schema"),
        }

    def _run_payload(self, run: dict[str, Any], *, include_logs: bool) -> dict[str, Any]:
        result = dict(run)
        result["params"] = self._json_value(result.pop("params_json", "{}"), {})
        result["result"] = self._json_value(result.pop("result_json", "{}"), {})
        if not include_logs:
            stdout = result.pop("stdout", "")
            stderr = result.pop("stderr", "")
            result["stdout_preview"] = str(stdout)[:500]
            result["stderr_preview"] = str(stderr)[:500]
        return result

    def _json_value(self, value: Any, default: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value) if value else default
            except json.JSONDecodeError:
                return default
        return value if value is not None else default

    def _content_hash(
        self,
        code: str,
        *,
        language: str = "python",
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> str:
        fingerprint = json.dumps(
            {
                "code": code,
                "language": language,
                "input_schema": input_schema,
                "output_schema": output_schema,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    def _validate_schema(
        self, schema_name: str, schema: dict[str, Any], *, require_object_root: bool = False
    ) -> dict[str, Any]:
        if require_object_root and schema.get("type") != "object":
            raise ValidationError(f"{schema_name} 根类型必须为 object")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValidationError(f"{schema_name} 非法: {exc.message}") from exc
        return schema

    def _validate_script_params(
        self, script_key: str, input_schema: dict[str, Any], script_params: dict[str, Any]
    ) -> None:
        errors = sorted(
            Draft202012Validator(input_schema).iter_errors(script_params),
            key=lambda item: list(item.absolute_path),
        )
        if not errors:
            return
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "<root>"
        expected = json.dumps(input_schema, ensure_ascii=False, separators=(",", ":"))
        raise ValidationError(
            f"script params invalid script={script_key} field={path}: {first.message}; expected_schema={expected}"
        )

    def _validate_script_output(
        self,
        script_key: str,
        output_schema: dict[str, Any] | None,
        result: dict[str, Any],
    ) -> str | None:
        if output_schema is None:
            return None
        errors = sorted(
            Draft202012Validator(output_schema).iter_errors(result),
            key=lambda item: list(item.absolute_path),
        )
        if not errors:
            return None
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "<root>"
        expected = json.dumps(output_schema, ensure_ascii=False, separators=(",", ":"))
        return (
            f"output_schema invalid script={script_key} field={path}: "
            f"{first.message}; expected_schema={expected}"
        )

    def _bounded(self, value: str | bytes) -> str:
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = value
        if len(text) <= MAX_CAPTURE_CHARS:
            return text
        return text[:MAX_CAPTURE_CHARS] + "\n...[truncated]"

    def _runtime_env(self, actor: str, envelope: dict[str, Any]) -> dict[str, str]:
        try:
            config = load_server_config(self.paths)
            base_url = f"http://127.0.0.1:{config.port}"
        except Exception:
            base_url = ""
        workflow = envelope.get("workflow") if isinstance(envelope.get("workflow"), dict) else {}
        return {
            "AGENT_BRIDGE_API_BASE": base_url,
            "AGENT_BRIDGE_USER": actor,
            "AGENT_BRIDGE_PROFILE": str(envelope.get("profile_key") or ""),
            "AGENT_BRIDGE_WORKFLOW": "true" if workflow.get("enabled") else "false",
            "AGENT_BRIDGE_WORKFLOW_KEY": str(workflow.get("workflow_key") or ""),
            "AGENT_BRIDGE_WORKFLOW_RUN_ID": str(workflow.get("run_id") or ""),
            "PYTHONIOENCODING": "utf-8",
        }
