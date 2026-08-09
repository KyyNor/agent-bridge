# 模型评估 Docker 镜像

Agent Bridge 维护两份镜像：

- `opencompass-runner`：C-Eval、MMLU-Pro、GSM8K、IFEval，以及 HumanEval/MBPP 的代码生成；
- `agent-worker`：无网络代码沙箱和 SWE-bench Agent 协议入口。

不在运行时下载数据。OpenCompass、HumanEval 和 MBPP 数据应在构建前放入对应 Dockerfile 旁的 `datasets/` 目录，Dockerfile 会在构建期校验，缺少数据时直接失败。SWE-bench manifest 不随 `agent-worker` 构建，而是在运行时以只读 bind mount 提供。

```text
opencompass/datasets/
├─ opencompass/
│  ├─ dataset-manifest.json
│  └─ data/
│     ├─ ceval/formal_ceval/...
│     ├─ mmlu_pro/...
│     ├─ gsm8k/...
│     └─ ifeval/input_data.jsonl
└─ code/
   ├─ humaneval.jsonl
   └─ mbpp.jsonl

AGENT_BRIDGE_ROOT/data/model-evaluation/
└─ swebench-manifest.json
```

`dataset-manifest.json` 至少记录 OpenCompass 版本、各数据集的来源 revision、文件 hash 和构建日期。`swebench-manifest.json` 记录 task metadata revision 及每个 task 所需的本地 testbed 镜像，例如：

```json
{
  "version": "swe-bench-lite-v1",
  "tasks": [{
    "instance_id": "repo__repo-123",
    "problem_statement": "...",
    "testbed_image": "agent-bridge/swe-testbed:repo-123",
    "workdir": "/testbed",
    "test_command": "pytest -q"
  }]
}
```

SWE-bench 的 task metadata 默认从 `AGENT_BRIDGE_ROOT/data/model-evaluation/swebench-manifest.json` 读取，服务会在启动 `agent-worker` 时把该文件以只读方式挂入容器。可设置 `AGENT_BRIDGE_EVAL_SWEBENCH_MANIFEST=/srv/agent-bridge/swebench-manifest.json` 覆盖默认位置。更新 manifest 不需要重编 `agent-worker` 镜像，但只影响后续新发起的评测。每个任务的 testbed 镜像应提前 `docker load` 到部署机。Agent worker 通过 JSONL 向主服务请求启动 testbed、执行命令和最终测试，镜像本身不持有 Docker Socket。

当前 SWE Agent 单次模型请求上限为 6 分钟、最多 40 轮；主服务对每条 Agent 命令限制 3 分钟，对最终验收限制 15 分钟。没有单题总时长上限。修改 `agent-worker/scripts/swe-agent` 后需要重建 `agent-bridge-agent-worker` 镜像。

```bash
docker build -t agent-bridge-opencompass-runner:latest docker/model-evaluation/opencompass
docker build -t agent-bridge-agent-worker:latest docker/model-evaluation/agent-worker
```

运行时由 Agent Bridge 通过以下环境变量定位镜像：

```bash
export AGENT_BRIDGE_EVAL_OPENCOMPASS_IMAGE=agent-bridge-opencompass-runner:latest
export AGENT_BRIDGE_EVAL_AGENT_WORKER_IMAGE=agent-bridge-agent-worker:latest
```
