from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.memory_management.claude_mem.config import ClaudeMemConfigManager
from agent_bridge.memory_management.claude_mem.client import ClaudeMemClient
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT
from agent_bridge.plugin_runtime import GitPluginRuntime


DEFAULT_WORKER_HOST = "127.0.0.1"
DEFAULT_WORKER_PORT_BASE = 37700


class ClaudeMemWorkerService:
    def __init__(self, *, paths) -> None:
        self.paths = paths
        self.plugin_runtime = GitPluginRuntime(paths)
        self.config = ClaudeMemConfigManager(paths=paths)
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
        try:
            base_url = self._ensure_worker(block, plugin_dir=plugin_dir, timeout_seconds=8)
        except Exception as exc:
            log_path = self._worker_log_path(block)
            return {
                "status": "worker_error",
                "base_url": self._state_base_url(block) or self._configured_base_url(block),
                "plugin_dir": str(plugin_dir),
                "message": str(exc),
                "log_path": str(log_path),
                "log_tail": self._tail_log(log_path),
            }
        return {"status": "worker_ready", "base_url": base_url, "plugin_dir": str(plugin_dir)}

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
        if action != "version-check":
            try:
                self._ensure_worker(block, plugin_dir=plugin_dir, timeout_seconds=min(timeout_seconds, 15))
            except Exception as exc:
                return {
                    "stdout": NOOP_HOOK_STDOUT,
                    "stderr": str(exc),
                    "exit_code": 0,
                    "status": "worker_error",
                }
            if action == "start":
                return {"stdout": NOOP_HOOK_STDOUT, "stderr": "", "exit_code": 0, "status": "ok"}
        env = os.environ.copy()
        env["CLAUDE_MEM_DATA_DIR"] = str(block["data_dir"])
        self._apply_worker_env(env, block, self._worker_port(block, state=self._read_worker_state(block)), plugin_dir)
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
        base_url = self._ensure_worker(block)
        if not base_url:
            raise RuntimeError("claude-mem worker URL is not configured")
        existing = self._clients.get(block_key)
        if existing is not None and existing.base_url == base_url.rstrip("/"):
            return existing
        client = ClaudeMemClient(base_url)
        self._clients[block_key] = client
        return client

    def _configured_base_url(self, block: dict[str, Any]) -> str:
        explicit = str(block.get("worker_base_url") or "").strip()
        if explicit:
            return explicit
        return os.environ.get("CLAUDE_MEM_WORKER_URL", "").strip()

    def _ensure_worker(
        self,
        block: dict[str, Any],
        *,
        plugin_dir: Path | None = None,
        timeout_seconds: int = 12,
    ) -> str:
        configured = self._configured_base_url(block)
        if configured:
            if not self._worker_ready(configured):
                raise RuntimeError(f"configured claude-mem worker is not ready: {configured}")
            return configured

        plugin_dir = plugin_dir or self._plugin_dir()
        if plugin_dir is None:
            raise RuntimeError("claude-mem plugin scripts were not found on the server")
        worker_script = plugin_dir / "scripts" / "worker-service.cjs"
        if not worker_script.exists():
            raise RuntimeError(f"worker-service.cjs was not found at {worker_script}")

        state = self._read_worker_state(block)
        port = self._worker_port(block, state=state)
        base_url = self._base_url_for_port(port)
        pid = int(state.get("pid") or 0) if isinstance(state, dict) else 0
        if pid and self._pid_alive(pid) and self._worker_ready(base_url):
            return base_url
        if self._worker_ready(base_url):
            return base_url

        port = self._available_worker_port(block, start_port=port)
        base_url = self._base_url_for_port(port)
        process = self._start_worker_process(block, plugin_dir=plugin_dir, port=port)
        self._write_worker_state(
            block,
            {
                "pid": process.pid,
                "port": port,
                "base_url": base_url,
                "data_dir": str(Path(str(block["data_dir"])).expanduser()),
                "plugin_dir": str(plugin_dir),
                "started_at": time.time(),
            },
        )
        if self._wait_until_ready(base_url, process=process, timeout_seconds=timeout_seconds):
            return base_url
        raise RuntimeError(
            f"claude-mem worker did not become ready at {base_url}; log: {self._worker_log_path(block)}"
        )

    def _start_worker_process(self, block: dict[str, Any], *, plugin_dir: Path, port: int) -> subprocess.Popen:
        bun = self._bun_command()
        data_dir = Path(str(block["data_dir"])).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._worker_log_path(block)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        self._apply_worker_env(env, block, port, plugin_dir)
        worker_script = plugin_dir / "scripts" / "worker-service.cjs"
        log_file = log_path.open("ab")
        try:
            return subprocess.Popen(
                [bun, str(worker_script)],
                cwd=plugin_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        finally:
            log_file.close()

    def _apply_worker_env(self, env: dict[str, str], block: dict[str, Any], port: int, plugin_dir: Path) -> None:
        env["CLAUDE_MEM_DATA_DIR"] = str(Path(str(block["data_dir"])).expanduser())
        env["CLAUDE_MEM_WORKER_HOST"] = DEFAULT_WORKER_HOST
        env["CLAUDE_MEM_WORKER_PORT"] = str(port)
        env["CLAUDE_MEM_PLUGIN_ROOT"] = str(plugin_dir)
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_dir)
        env["PLUGIN_ROOT"] = str(plugin_dir)
        self.config.apply_to_env(env)

    def _bun_command(self) -> str:
        bun = shutil.which("bun")
        if bun:
            return bun
        home_bun = Path.home() / ".bun" / "bin" / "bun"
        if home_bun.exists():
            return str(home_bun)
        raise RuntimeError("bun was not found on PATH; install bun on the server before starting claude-mem")

    def _worker_port(self, block: dict[str, Any], *, state: dict[str, Any] | None = None) -> int:
        env_port = os.environ.get("CLAUDE_MEM_WORKER_PORT", "").strip()
        if env_port:
            return int(env_port)
        if state and state.get("port"):
            return int(state["port"])
        block_key = str(block["block_key"])
        digest = hashlib.sha256(block_key.encode("utf-8")).hexdigest()
        return DEFAULT_WORKER_PORT_BASE + (int(digest[:6], 16) % 1000)

    def _available_worker_port(self, block: dict[str, Any], *, start_port: int) -> int:
        if os.environ.get("CLAUDE_MEM_WORKER_PORT", "").strip():
            base_url = self._base_url_for_port(start_port)
            if self._worker_ready(base_url) or not self._port_in_use(start_port):
                return start_port
            raise RuntimeError(f"configured claude-mem worker port is already in use: {start_port}")
        port = start_port
        for offset in range(1000):
            candidate = DEFAULT_WORKER_PORT_BASE + ((port - DEFAULT_WORKER_PORT_BASE + offset) % 1000)
            base_url = self._base_url_for_port(candidate)
            if self._worker_ready(base_url) or not self._port_in_use(candidate):
                return candidate
        raise RuntimeError("no available local port was found for claude-mem worker")

    def _base_url_for_port(self, port: int) -> str:
        return f"http://{DEFAULT_WORKER_HOST}:{port}"

    def _state_base_url(self, block: dict[str, Any]) -> str:
        state = self._read_worker_state(block)
        return str(state.get("base_url") or "") if isinstance(state, dict) else ""

    def _worker_ready(self, base_url: str) -> bool:
        try:
            response = httpx.get(f"{base_url.rstrip('/')}/api/readiness", timeout=0.8)
            if 200 <= response.status_code < 300:
                return True
        except Exception:
            pass
        try:
            response = httpx.get(f"{base_url.rstrip('/')}/api/health", timeout=0.8)
            return 200 <= response.status_code < 300
        except Exception:
            return False

    def _wait_until_ready(self, base_url: str, *, process: subprocess.Popen, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._worker_ready(base_url):
                return True
            if process.poll() is not None:
                raise RuntimeError(
                    f"claude-mem worker exited with code {process.returncode}; log: {self._worker_log_path_from_url(base_url)}"
                )
            time.sleep(0.2)
        return False

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _port_in_use(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex((DEFAULT_WORKER_HOST, port)) == 0

    def _worker_state_path(self, block: dict[str, Any]) -> Path:
        safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(block["block_key"]))
        return self.paths.run_dir / "claude-mem-workers" / f"{safe_key}.json"

    def _worker_log_path(self, block: dict[str, Any]) -> Path:
        safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(block["block_key"]))
        return self.paths.logs_dir / "claude-mem-workers" / f"{safe_key}.log"

    def _worker_log_path_from_url(self, base_url: str) -> Path:
        for state_path in (self.paths.run_dir / "claude-mem-workers").glob("*.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if state.get("base_url") == base_url:
                return self.paths.logs_dir / "claude-mem-workers" / f"{state_path.stem}.log"
        return self.paths.logs_dir / "claude-mem-workers" / "unknown.log"

    def _read_worker_state(self, block: dict[str, Any]) -> dict[str, Any]:
        state_path = self._worker_state_path(block)
        if not state_path.exists():
            return {}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_worker_state(self, block: dict[str, Any], state: dict[str, Any]) -> None:
        state_path = self._worker_state_path(block)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _tail_log(self, log_path: Path, *, max_chars: int = 4000) -> str:
        if not log_path.exists():
            return ""
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        return text[-max_chars:]

    def _plugin_dir(self) -> Path | None:
        explicit = os.environ.get("CLAUDE_MEM_PLUGIN_ROOT", "").strip()
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        candidates.append(self.plugin_runtime.plugin_repo_dir("claude-mem"))
        candidates.append(self.plugin_runtime.plugin_repo_dir("claude-mem") / "plugin")
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

    def ensure_plugin(self, git_url: str) -> dict[str, Any]:
        result = self.plugin_runtime.ensure_repo(plugin_key="claude-mem", git_url=git_url)
        if result["status"] in {"failed", "skipped"}:
            return result
        plugin_dir = self._managed_plugin_dir()
        if plugin_dir is None:
            return {
                "status": "failed",
                "plugin_key": "claude-mem",
                "repo_dir": str(self.plugin_runtime.plugin_repo_dir("claude-mem")),
                "message": "claude-mem plugin directory was not found after clone/update",
            }
        install = self.plugin_runtime.run_install(
            plugin_key="claude-mem",
            cwd=plugin_dir,
            command=["bun", "install"],
        )
        if install["status"] == "failed":
            return install
        return {**result, "install": install, "plugin_dir": str(plugin_dir)}

    def _managed_plugin_dir(self) -> Path | None:
        repo_dir = self.plugin_runtime.plugin_repo_dir("claude-mem")
        for candidate in (repo_dir / "plugin", repo_dir):
            if (candidate / "scripts").is_dir():
                return candidate
        return None
