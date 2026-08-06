# syntax=docker/dockerfile:1
# 同一镜像以不同 entrypoint 运行无网络代码沙箱或 SWE-bench Agent。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_BRIDGE_SWE_DATA_ROOT=/opt/agent-bridge-data/swebench

RUN useradd --create-home --uid 10001 evaluator \
    && mkdir -p /opt/agent-bridge-data/swebench

# SWE-bench 元数据和 testbed 映射在构建前放入 datasets/swebench/。
COPY datasets/swebench/ /opt/agent-bridge-data/swebench/
COPY scripts/code-sandbox /usr/local/bin/code-sandbox
COPY scripts/swe-agent /usr/local/bin/swe-agent
COPY scripts/verify-datasets /usr/local/bin/verify-agent-worker-datasets

RUN chmod 0555 /usr/local/bin/code-sandbox /usr/local/bin/swe-agent /usr/local/bin/verify-agent-worker-datasets \
    && /usr/local/bin/verify-agent-worker-datasets \
    && chown -R evaluator:evaluator /opt/agent-bridge-data

USER evaluator
WORKDIR /workspace
