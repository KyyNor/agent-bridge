from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from agent_bridge.memory_management.claude_mem.client import ClaudeMemClient
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


class ClaudeMemWorkerService:
    def __init__(self, *, paths) -> None:
        self.paths = paths
        self._clients: dict[str, ClaudeMemClient] = {}

    def health(self, block: dict[str, Any]) -> dict[str, Any]:
        plugin_dir = self._plugin_dir()
        if plugin_dir is None:
            return {
                "status": "claude_mem_not_installed",
                "message": "claude-mem plugin scripts were not found on the server",
            }
        if not (plugin_dir / "scripts" / "worker-service.cjs").exists():
            return {"status": "claude_mem_not_installed", "message": "worker-service.cjs was not found"}
        base_url = self._base_url(block)
        return {"status": "worker_ready" if base_url else "worker_starting", "base_url": base_url, "plugin_dir": str(plugin_dir)}

    def search(self, block: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
        try:
            result = self._client(block).search(query, limit)
        except Exception as exc:
            return {"status": "worker_error", "block_key": block["block_key"], "items": [], "error": str(exc)}
        return {"status": "ok", "block_key": block["block_key"], "items": result["items"]}

    def timeline(self, block: dict[str, Any], *, limit: int, cursor: str | None) -> dict[str, Any]:
        try:
            result = self._client(block).timeline(limit, cursor)
        except Exception as exc:
            return {
                "status": "worker_error",
                "block_key": block["block_key"],
                "items": [],
                "next_cursor": None,
                "error": str(exc),
            }
        return {
            "status": "ok",
            "block_key": block["block_key"],
            "items": result["items"],
            "next_cursor": result["next_cursor"],
        }

    def get_observation(self, block: dict[str, Any], observation_id: str) -> dict[str, Any]:
        try:
            result = self._client(block).get_observation(observation_id)
        except Exception as exc:
            return {"status": "worker_error", "block_key": block["block_key"], "item": None, "error": str(exc)}
        return {"status": "ok", "block_key": block["block_key"], "item": result["item"]}

    def handle_hook(
        self,
        block: dict[str, Any],
        *,
        action: str,
        payload: dict[str, Any],
        event_name: str | None,
        matcher: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        plugin_dir = self._plugin_dir()
        if plugin_dir is None:
            return {
                "stdout": NOOP_HOOK_STDOUT,
                "stderr": "claude-mem plugin scripts were not found on the server",
                "exit_code": 0,
                "status": "claude_mem_not_installed",
            }
        env = os.environ.copy()
        env["CLAUDE_MEM_DATA_DIR"] = str(block["data_dir"])
        hook_payload = dict(payload)
        if event_name is not None:
            hook_payload.setdefault("hook_event_name", event_name)
        if matcher is not None:
            hook_payload.setdefault("matcher", matcher)
        try:
            completed = subprocess.run(
                self._hook_command(plugin_dir, action),
                input=json.dumps(hook_payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
        except Exception as exc:
            return {"stdout": NOOP_HOOK_STDOUT, "stderr": str(exc), "exit_code": 0, "status": "worker_error"}
        stdout = completed.stdout.strip() or NOOP_HOOK_STDOUT
        return {
            "stdout": stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
            "status": "ok" if completed.returncode == 0 else "worker_error",
        }

    def _hook_command(self, plugin_dir: Path, action: str) -> list[str]:
        scripts = plugin_dir / "scripts"
        if action == "version-check":
            return ["node", str(scripts / "version-check.js")]
        mode = "start" if action == "start" else "hook"
        trailing = [] if action == "start" else ["claude-code", action]
        return ["node", str(scripts / "bun-runner.js"), str(scripts / "worker-service.cjs"), mode, *trailing]

    def _client(self, block: dict[str, Any]) -> ClaudeMemClient:
        block_key = str(block["block_key"])
        base_url = self._base_url(block)
        if not base_url:
            raise RuntimeError("claude-mem worker URL is not configured")
        existing = self._clients.get(block_key)
        if existing is not None and existing.base_url == base_url.rstrip("/"):
            return existing
        client = ClaudeMemClient(base_url)
        self._clients[block_key] = client
        return client

    def _base_url(self, block: dict[str, Any]) -> str:
        explicit = str(block.get("worker_base_url") or "").strip()
        if explicit:
            return explicit
        return os.environ.get("CLAUDE_MEM_WORKER_URL", "").strip()

    def _plugin_dir(self) -> Path | None:
        explicit = os.environ.get("CLAUDE_MEM_PLUGIN_ROOT", "").strip()
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
        cache_root = claude_dir / "plugins" / "cache" / "thedotmack" / "claude-mem"
        if cache_root.exists():
            candidates.extend(sorted([p for p in cache_root.iterdir() if p.is_dir()], reverse=True))
        candidates.append(claude_dir / "plugins" / "marketplaces" / "thedotmack" / "plugin")
        for candidate in candidates:
            plugin_dir = candidate / "plugin" if (candidate / "plugin" / "scripts").exists() else candidate
            if (plugin_dir / "scripts" / "bun-runner.js").exists() and (
                plugin_dir / "scripts" / "worker-service.cjs"
            ).exists():
                return plugin_dir
        return None
