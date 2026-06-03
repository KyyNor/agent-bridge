# Agent Capability Hub

`Agent Capability Hub` is a Python 3.11 service for registering HTTP MCP services,
syncing their tool definitions, and exposing a stable MetaMCP gateway for agents.

Phase 1 focuses on:

- HTTP MCP service registration
- MCP tool list synchronization
- a lightweight web registration page at `/admin/capabilities`
- MetaMCP `search` for registry browsing with `path` and `query`
- MetaMCP `execute` for read-only tool execution
- preserving existing wiki-manager knowledge-base functionality as a capability source foundation

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
uv run wiki server start
uv run wiki server init
open http://127.0.0.1:8765/admin/capabilities
uv run wiki kb create frontend-docs --name "Frontend Docs"
uv run wiki kb grant frontend-docs alice contributor
uv run wiki add ./Guide.pdf --kb frontend-docs --later
uv run wiki sync
uv run wiki docs --kb frontend-docs
uv run wiki status
```

By default the service stores configuration, data, logs, and pid files under `/root/wiki-manager`.
The user running `wiki server start` must be able to create and write `/root/wiki-manager/config`,
`/root/wiki-manager/data`, `/root/wiki-manager/logs`, and `/root/wiki-manager/run`; in the target deployment this is usually started by `root`.
The first phase trusts the Linux username sent by the CLI in `X-Wiki-User` and is intended for an internal trusted VM.

## Agent Capability Hub Usage

```bash
uv run wiki server start
uv run wiki server init
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
  "tool": "query_sql",
  "arguments": {
    "db": "whjcbb",
    "sql": "select abc from aaa",
    "limit": 10
  }
}
```
