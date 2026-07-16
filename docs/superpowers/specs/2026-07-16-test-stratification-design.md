# Test Stratification and Parallel Execution

## Goal

Make the default local test command fast and parallel while keeping live-service, real-process, and real-CodeGraph coverage available as explicit suites.

## Design

- Keep `ragflow` and `weknora` as explicit live integration markers.
- Add `e2e`, `codegraph_cli`, and `process` markers for tests that should not run in the daily fast lane.
- Add `pytest-xdist` as a development dependency, but do not enable parallelism globally; only the fast command uses `-n auto`.
- Provide a small script-based test entry point with `fast`, `full`, and `integration` modes. The fast mode excludes only the marked complex tests; full mode preserves the existing complete suite; integration mode runs live-service tests serially.
- Mark only tests that actually start external processes, invoke the real CodeGraph CLI, or exercise end-to-end flows. Ordinary unit, API, storage, and mocked backend tests remain in the fast lane.

## Safety

Most tests use per-test `tmp_path` roots, so xdist workers receive isolated databases, logs, and repositories. Live-service and process tests remain out of the parallel fast lane because they share external services or depend on ports and health checks.

## Verification

Verify marker selection with pytest collection, run the fast suite with xdist, and run the full suite without changing its selection semantics. Confirm that no existing business source files are modified by this change.
