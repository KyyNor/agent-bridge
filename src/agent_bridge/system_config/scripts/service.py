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

from agent_bridge.core.config import AgentBridgePaths, load_server_config
from agent_bridge.core.domain import NotFound, ValidationError, require_admin_user
from agent_bridge.storage.sqlite import SQLiteStore
from agent_bridge.system_config.scripts.runtime_support import render_runner, render_runtime_helper


SCRIPT_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
OWNER_TYPES = {"workflow", "skill", "profile", "system"}
SCRIPT_STATUSES = {"active", "disabled"}
RUN_TYPES = {"test", "mcp"}
MAX_CAPTURE_CHARS = 256_000


@dataclass(frozen=True)
class ScriptProcessResult:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool = False


class ScriptService:
    def __init__(self, *, paths: AgentBridgePaths, store: SQLiteStore, admins: set[str]) -> None:
        self.paths = paths
        self.store = store
        self.admins = admins
        self.base_run_dir = paths.run_dir / "scripts"

    def list_scripts(self, actor: str) -> list[dict[str, Any]]:
        require_admin_user(actor, self.admins)
        return [self._script_payload(script, include_code=False) for script in self.store.scripts.list_scripts()]

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
    ) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        normalized_key = self._validate_script_key(script_key)
        normalized_language = language.strip().lower()
        if normalized_language != "python":
            raise ValidationError("only python scripts are supported")
        normalized_status = self._validate_status(status)
        normalized_owner_type, normalized_owner_key = self._validate_owner(owner_type, owner_key)
        normalized_code = code.rstrip() + "\n"
        if not normalized_code.strip():
            raise ValidationError("code is required")
        normalized_name = name.strip() or normalized_key
        if input_schema is None:
            raise ValidationError("input_schema is required")
        normalized_input_schema = self._validate_input_schema(input_schema)
        script = self.store.scripts.upsert_script(
            script_key=normalized_key,
            name=normalized_name,
            description=description.strip(),
            language=normalized_language,
            code=normalized_code,
            input_schema=normalized_input_schema,
            status=normalized_status,
            owner_type=normalized_owner_type,
            owner_key=normalized_owner_key,
            content_hash=self._content_hash(normalized_code),
            actor=actor,
        )
        return self._script_payload(script, include_code=True)

    def delete_script(self, actor: str, script_key: str) -> dict[str, Any]:
        require_admin_user(actor, self.admins)
        deleted = self.store.scripts.delete_script(self._validate_script_key(script_key))
        if not deleted:
            raise NotFound("script not found")
        return {"script_key": script_key, "deleted": True}

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
        run = self.store.scripts.create_script_run(
            run_id=run_id,
            script_key=script["script_key"],
            run_type=run_type,
            params={"script_params": script_params, "timeout_seconds": timeout, "profile_key": profile_key},
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
        script = self.store.scripts.get_script(self._validate_script_key(script_key))
        if script is None:
            raise NotFound("script not found")
        return script

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
        if not include_code:
            payload.pop("code", None)
            payload["code_preview"] = str(script.get("code") or "")[:160]
        return payload

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

    def _content_hash(self, code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def _validate_input_schema(self, input_schema: dict[str, Any]) -> dict[str, Any]:
        if input_schema.get("type") != "object":
            raise ValidationError("input_schema 根类型必须为 object")
        try:
            Draft202012Validator.check_schema(input_schema)
        except SchemaError as exc:
            raise ValidationError(f"input_schema 非法: {exc.message}") from exc
        return input_schema

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
