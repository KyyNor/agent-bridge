# syntax=docker/dockerfile:1
# 同一镜像以不同 entrypoint 运行无网络代码沙箱或 SWE-bench Agent。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_BRIDGE_SWE_DATA_ROOT=/opt/agent-bridge-data/swebench

RUN useradd --create-home --uid 10001 evaluator \
    && mkdir -p /opt/agent-bridge-data/swebench

# SWE-bench 元数据在运行时以只读 bind mount 挂载到 /opt/agent-bridge-data/swebench/。
COPY scripts/code-sandbox /usr/local/bin/code-sandbox
COPY scripts/swe-agent /usr/local/bin/swe-agent

RUN chmod 0555 /usr/local/bin/code-sandbox /usr/local/bin/swe-agent \
    && chown -R evaluator:evaluator /opt/agent-bridge-data

USER evaluator
WORKDIR /workspace
