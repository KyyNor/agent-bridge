# wiki-manager

`wiki-manager` is a Python 3.11 CLI and local service for managing an internal knowledge-base ingestion ledger.

Phase 1 focuses on:

- logical knowledge bases
- Linux-user based KB permissions
- original document archiving
- immutable document versions
- immediate and planned sync jobs
- a local mock backend

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
uv run wiki kb create frontend-docs --name "Frontend Docs"
uv run wiki kb grant frontend-docs alice contributor
uv run wiki add ./Guide.pdf --kb frontend-docs --later
uv run wiki sync
uv run wiki docs --kb frontend-docs
uv run wiki status
```

By default the service stores configuration, data, logs, and pid files under `/root/wiki-manager`.
The first phase trusts the Linux username sent by the CLI in `X-Wiki-User` and is intended for an internal trusted VM.
