"""与 Profile 绑定、但不由 ``profile use`` 自动安装的 Hook 命令。"""

from __future__ import annotations

import getpass
import json
import logging
import sys
from typing import Annotated

import typer

from agent_bridge.client import AgentBridgeClient
from agent_bridge.core.config import default_user
from agent_bridge.knowledge_management.memory.models import NOOP_HOOK_STDOUT


logger = logging.getLogger(__name__)
RETRIEVAL_PROBE_TIMEOUT_SECONDS = 20
RETRIEVAL_PROBE_CLIENT_TIMEOUT_SECONDS = 22.0

profile_hook_app = typer.Typer(
    help="手工调用的 Profile Hook",
    no_args_is_help=True,
)
claude_code_hook_app = typer.Typer(
    help="Claude Code Profile Hook",
    no_args_is_help=True,
)
profile_hook_app.add_typer(claude_code_hook_app, name="claude-code")

@claude_code_hook_app.command("retrieval-probe")
def retrieval_probe_hook(
    profile: Annotated[
        str,
        typer.Option("--profile", help="Agent Bridge profile key"),
    ],
    server_url: Annotated[
        str,
        typer.Option("--server-url", help="Agent Bridge API base URL"),
    ] = "http://127.0.0.1:8765",
    timeout: Annotated[
        int,
        typer.Option("--timeout", min=1, max=30, help="探测超时秒数"),
    ] = RETRIEVAL_PROBE_TIMEOUT_SECONDS,
    hook_id: Annotated[
        str,
        typer.Option("--agent-bridge-hook-id", hidden=True),
    ] = "",
) -> None:
    del hook_id
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return
        if payload.get("hook_event_name") != "UserPromptSubmit":
            return
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return
        client = AgentBridgeClient(server_url, default_user(getpass.getuser()))
        result = client.post_retrieval_probe_hook(
            {
                "profile_key": profile,
                "event_name": "UserPromptSubmit",
                "matcher": None,
                "payload": payload,
                "hook_timeout_seconds": timeout,
            },
            timeout=RETRIEVAL_PROBE_CLIENT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "Claude Code 检索探测 Hook 调用失败 profile=%s 原因=%s",
            profile,
            exc,
        )
        typer.echo(NOOP_HOOK_STDOUT)
        raise typer.Exit()

    stdout = str(result.get("stdout") or NOOP_HOOK_STDOUT)
    typer.echo(stdout)
    raise typer.Exit(int(result.get("exit_code") or 0))
