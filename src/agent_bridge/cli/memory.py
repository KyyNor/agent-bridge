from __future__ import annotations

import getpass
import json
import sys
from typing import Annotated

import typer

from agent_bridge.client import AgentBridgeClient
from agent_bridge.core.config import default_user
from agent_bridge.memory_management.models import NOOP_HOOK_STDOUT


memory_app = typer.Typer(help="管理记忆与 Claude Code hook 代理", no_args_is_help=True)
hook_app = typer.Typer(help="Claude Code hook 代理", no_args_is_help=True)
memory_app.add_typer(hook_app, name="hook")


@hook_app.command("claude-code")
def claude_code_hook(
    action: Annotated[str, typer.Argument(help="claude-mem hook action")],
    profile: Annotated[str, typer.Option("--profile", help="Agent Bridge profile key")],
    server_url: Annotated[str, typer.Option("--server-url", help="Agent Bridge API base URL")] = "http://127.0.0.1:8765",
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
