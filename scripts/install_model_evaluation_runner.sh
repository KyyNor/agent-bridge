#!/usr/bin/env bash
set -euo pipefail

# 在独立 venv 中安装 OpenCompass，避免其 httpx==0.27.2 与主服务 PageIndex 冲突。
RUNNER_DIR="${1:-/opt/agent-bridge-opencompass}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3.11 -m venv "$RUNNER_DIR"
"$RUNNER_DIR/bin/pip" install --upgrade pip
"$RUNNER_DIR/bin/pip" install -r "$SCRIPT_DIR/../requirements/model-evaluation-runner.txt"

echo "安装完成。设置 AGENT_BRIDGE_OPENCOMPASS_BIN=$RUNNER_DIR/bin/opencompass 后重启 Agent Bridge。"
