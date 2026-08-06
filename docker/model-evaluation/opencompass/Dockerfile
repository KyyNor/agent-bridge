# syntax=docker/dockerfile:1
# 构建上下文应为 docker/model-evaluation/opencompass；数据在构建前由脚本放入 datasets/。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMPASS_DATA_CACHE=/opt/opencompass-data \
    AGENT_BRIDGE_CODE_DATA_ROOT=/opt/agent-bridge-data/code

RUN useradd --create-home --uid 10001 evaluator \
    && pip install --no-cache-dir "opencompass[api]==0.5.3" \
    && mkdir -p /opt/opencompass-data /opt/agent-bridge-data/code

# 该目录必须包含固定版本的 C-Eval、MMLU-Pro、GSM8K、IFEval、HumanEval、MBPP 数据与 manifest。
COPY datasets/opencompass/ /opt/opencompass-data/
COPY datasets/code/ /opt/agent-bridge-data/code/
COPY scripts/agent-bridge-code-generate /usr/local/bin/agent-bridge-code-generate
COPY scripts/verify-datasets /usr/local/bin/verify-opencompass-datasets

RUN chmod 0555 /usr/local/bin/agent-bridge-code-generate /usr/local/bin/verify-opencompass-datasets \
    && /usr/local/bin/verify-opencompass-datasets \
    && chown -R evaluator:evaluator /opt/opencompass-data /opt/agent-bridge-data

USER evaluator
WORKDIR /workspace
