# Agent Bridge

`Agent Bridge` is a Python 3.11 service for registering HTTP MCP services,
syncing their tool definitions, and exposing a stable MetaMCP gateway for agents.
It also includes built-in knowledge management (wiki document sync) and
CodeGraph code repository indexing capabilities.

Features:

- HTTP MCP service registration and tool synchronization
- Capability governance (project profiles with allow/deny rules)
- Tool call logging and statistics
- MetaMCP `search` for registry browsing with `path` and `query`
- MetaMCP `execute` for governed tool execution
- Built-in Wiki knowledge base management
- Built-in CodeGraph repository indexing
- Vue 3 capability console at `/admin/capabilities`

## Setup

```bash
uv sync
```

## Run Tests

```bash
uv run pytest -v
```

## Local Usage

```bash
uv run agent-bridge server start
uv run agent-bridge server init
open http://127.0.0.1:8765/admin/capabilities
uv run agb wiki kb create frontend-docs --name "Frontend Docs"
uv run agb wiki kb grant frontend-docs alice contributor
uv run agb wiki add ./Guide.pdf --kb frontend-docs --later
uv run agb wiki sync
uv run agb wiki docs --kb frontend-docs
uv run agb wiki status
```

The short command `agb` is equivalent to `agent-bridge`.

By default the service stores configuration, data, logs, and pid files under `/root/agent-bridge`.
The user running `agent-bridge server start` must be able to create and write `/root/agent-bridge/config`,
`/root/agent-bridge/data`, `/root/agent-bridge/logs`, and `/root/agent-bridge/run`;
in the target deployment this is usually started by `root`.
The first phase trusts the Linux username sent by the CLI in `X-Agent-Bridge-User` and is intended for an internal trusted VM.

## MetaMCP Usage

```bash
uv run agent-bridge server start
uv run agent-bridge server init
open http://127.0.0.1:8765/admin/capabilities
```

MetaMCP `search` examples:

```json
{}
```

```json
{"query": "mysql"}
```

```json
{"path": "mysql"}
```

```json
{"path": "mysql", "query": "sql"}
```

MetaMCP `execute` example:

```json
{
  "service": "mysql",
  "tool_name": "query_sql",
  "params": {
    "db": "whjcbb",
    "sql": "select abc from aaa",
    "limit": 10
  }
}
```

## Governance Usage

```bash
uv run agent-bridge profile create safe-readonly --name "safe-readonly"
uv run agent-bridge profile rules safe-readonly --allow mysql --deny hive
uv run agent-bridge profile use safe-readonly --scope project --url http://127.0.0.1:8765/mcp
uv run agent-bridge profile config --scope project
```

## Frontend Development

```bash
cd frontend/capabilities
npm install
npm run dev       # start dev server with API proxy
npm run build     # typecheck + production build
```

The Vue 3 frontend builds to `src/agent_bridge/static/capabilities/`.
