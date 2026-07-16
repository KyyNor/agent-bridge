# Test Stratification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repository's daily test lane fast and parallel while preserving explicit full and live-integration lanes.

**Architecture:** Add pytest markers for end-to-end, real CodeGraph CLI, and real-process tests; keep live backend markers; add xdist only to the fast entry point. A root script will expose `fast`, `full`, and `integration` modes without changing the existing full-suite selection.

**Tech Stack:** Python 3.11, pytest, pytest-xdist, Bash, Node/tsx frontend tests.

---

### Task 1: Add marker and dependency configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` via `uv lock`

- [x] Add `pytest-xdist` to the development dependency group.
- [x] Register `e2e`, `codegraph_cli`, and `process` markers alongside `ragflow` and `weknora`.
- [x] Run pytest collection to confirm the configuration parses and markers are registered.

### Task 2: Mark complex tests precisely

**Files:**
- Modify: `tests/test_e2e.py`
- Modify: `tests/test_codegraph_service.py`
- Modify: `tests/test_builtin_codegraph.py`
- Modify: `tests/test_capability_api.py`
- Modify: `tests/test_scripts.py`

- [x] Mark the end-to-end flow module with `e2e`.
- [x] Mark real CodeGraph repository/indexing tests with `codegraph_cli`; leave mocked client tests in the fast lane.
- [x] Mark the two script tests that start a real uvicorn process with `process`.
- [x] Verify collection counts for the fast selection and the excluded selection.

### Task 3: Add test lane entry point

**Files:**
- Create: `scripts/test.sh`

- [x] Implement `fast`, `full`, and `integration` modes.
- [x] Run backend fast tests with `-n auto` and exclude only `e2e`, `codegraph_cli`, `process`, `ragflow`, and `weknora`.
- [x] Run the frontend test suite from the fast lane.
- [x] Keep full mode serial and selection-equivalent to `pytest tests`.
- [x] Keep integration mode serial and limited to the live-service test files.
- [x] Make the script executable and provide usage errors for unknown modes.

### Task 4: Install and verify the parallel lane

**Files:**
- No source changes.

- [x] Sync the development environment so `pytest-xdist` is available.
- [x] Run marker collection checks.
- [x] Run the fast lane and capture count, failures, and duration.
- [x] Run a small two-worker smoke subset to confirm xdist actually starts workers.
- [x] Confirm `git diff` contains only test configuration, markers, the test script, and the design/plan documents.

### Task 5: Verify full and integration lanes remain explicit

**Files:**
- No source changes.

- [x] Run the full backend suite with slow-test reporting, or record existing external-service failures if the environment cannot support it.
- [x] Verify the integration selector collects only `ragflow` and `weknora` tests.
- [x] Report the daily command and the separate commands for full/live coverage.
