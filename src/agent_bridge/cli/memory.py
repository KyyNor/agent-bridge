from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from agent_bridge.client import AgentBridgeClient
from agent_bridge.core.config import default_user
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


memory_app = typer.Typer(help="管理记忆与 Claude Code hook 代理", no_args_is_help=True)
hook_app = typer.Typer(help="Claude Code hook 代理", no_args_is_help=True)
memory_app.add_typer(hook_app, name="hook")


AGENT_BRIDGE_HOOK_MARKER = "--agent-bridge-hook-id agent-bridge-memory"


def _settings_has_agent_bridge_memory_hook(settings: dict) -> bool:
    raw_hooks = settings.get("hooks")
    if not isinstance(raw_hooks, dict):
        return False
    for entries in raw_hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                continue
            for hook in hooks:
                if (
                    isinstance(hook, dict)
                    and AGENT_BRIDGE_HOOK_MARKER in str(hook.get("command") or "")
                ):
                    return True
    return False


def _load_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _project_settings_candidates(project_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    try:
        home = Path.home().resolve()
    except OSError:
        home = None
    for root in (project_dir, *project_dir.parents):
        try:
            resolved_root = root.resolve()
        except OSError:
            resolved_root = root
        if home is not None and resolved_root == home:
            break
        candidates.extend(
            [
                root / ".claude" / "settings.local.json",
                root / ".claude" / "settings.json",
            ]
        )
    return candidates


def _project_has_agent_bridge_memory_hook(project_dir: Path) -> bool:
    for path in _project_settings_candidates(project_dir):
        if _settings_has_agent_bridge_memory_hook(_load_json_object(path)):
            return True
    return False


def _hook_project_dir(payload: dict) -> Path:
    env_project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_project_dir:
        return Path(env_project_dir)
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd)
    return Path.cwd()


@hook_app.command("claude-code")
def claude_code_hook(
    action: Annotated[str, typer.Argument(help="claude-mem hook action")],
    profile: Annotated[str, typer.Option("--profile", help="Agent Bridge profile key")],
    server_url: Annotated[str, typer.Option("--server-url", help="Agent Bridge API base URL")] = "http://127.0.0.1:8765",
    scope: Annotated[str, typer.Option("--scope", help="Agent Bridge hook scope", hidden=True)] = "",
    event: Annotated[str | None, typer.Option("--event", help="Claude Code hook event name")] = None,
    matcher: Annotated[str | None, typer.Option("--matcher", help="Claude Code hook matcher")] = None,
    timeout: Annotated[int, typer.Option("--timeout", help="Hook timeout seconds")] = 60,
    agent_bridge_hook_id: Annotated[
        str,
        typer.Option("--agent-bridge-hook-id", help="Internal hook marker", hidden=True),
    ] = "",
) -> None:
    del agent_bridge_hook_id
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        if scope == "user" and _project_has_agent_bridge_memory_hook(_hook_project_dir(payload)):
            raise typer.Exit(0)
        client = AgentBridgeClient(server_url, default_user(getpass.getuser()))
        result = client.post_memory_hook(
            action,
            {
                "profile_key": profile,
                "event_name": event,
                "matcher": matcher,
                "payload": payload,
                "hook_timeout_seconds": timeout,
                "source": "claude-code",
            },
            timeout=float(timeout + 5),
        )
        stdout = str(result.get("stdout") or NOOP_HOOK_STDOUT)
        if stdout:
            typer.echo(stdout)
        raise typer.Exit(int(result.get("exit_code") or 0))
    except typer.Exit:
        raise
    except Exception:
        typer.echo(NOOP_HOOK_STDOUT)
        raise typer.Exit(0) from None
