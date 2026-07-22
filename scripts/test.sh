#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
    PYTHON="${PYTHON_FALLBACK:-python}"
fi

MODE="${1:-fast}"
if [[ $# -gt 0 ]]; then
    shift
fi

run_frontend() {
    (
        cd "$ROOT_DIR/frontend/capabilities"
        npm run "$1"
    )
}

case "$MODE" in
    fast)
        "$PYTHON" -m pytest "$ROOT_DIR/tests" \
            -m "not e2e and not codegraph_cli and not process and not ragflow and not weknora" \
            -n auto "$@"
        run_frontend test
        ;;
    full)
        "$PYTHON" -m pytest "$ROOT_DIR/tests" "$@"
        run_frontend check
        ;;
    integration)
        "$PYTHON" -m pytest \
            "$ROOT_DIR/tests/test_ragflow_integration.py" \
            "$ROOT_DIR/tests/test_weknora_integration.py" \
            -m "ragflow or weknora" "$@"
        ;;
    *)
        echo "Usage: $0 {fast|full|integration} [pytest arguments...]" >&2
        exit 2
        ;;
esac
