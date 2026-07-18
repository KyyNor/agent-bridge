# 工作流增量执行与历史产物复用设计

## 1. 背景与目标

当前工作流以整体 workflow revision 管理定义版本，任务以 (workflow_key, task_key, task_version) 管理。每次工作流执行都会创建新的 workflow_run，节点执行结果保存在 workflow_node_runs.output_json，显式文件产物保存在 workflow_artifacts。

本设计在不破坏历史运行和历史产物的前提下，引入节点级增量执行：工作流定义发生变化时，只重新执行受影响节点及其下游节点，未受影响节点复用同一任务版本的历史输出。

目标：

- 任务版本变化时强制全量执行；
- 任务版本不变、工作流版本变化时按 DAG 依赖增量执行；
- 工作流版本和任务版本都未变化时允许复用完整历史结果；
- 支持 stale 任务状态；
- 支持执行前预览复用/重跑计划；
- 支持页面运行按钮触发增量执行、普通执行和强制全量执行；
- 历史运行、历史节点输出和历史 artifact 保持只读且可追溯；
- 多个并发 run 之间不覆盖或污染彼此的产物。

## 2. 非目标

- 不引入用户手工维护的节点版本号；
- 不改变已有任务版本的身份规则；
- 不把不同 task_version 的产物混合使用；
- 不因为 workflow 保存而立即自动启动新的执行；
- 不要求建立独立的全局 node cache 表，历史成功节点记录先作为缓存源；
- 不改变已有的任务租约、失败重试和调度窗口机制，只扩展其可领取状态。

## 3. 术语与不变量

### 3.1 版本层次

| 名称 | 含义 | 复用作用 |
| --- | --- | --- |
| task_version | workflow_tasks 中同一任务 key 的业务版本 | 必须严格一致，不能跨任务版本复用 |
| workflow_revision_no | workflow 定义的整体修订号 | 决定是否需要重新计算增量计划 |
| node_semantic_fingerprint | 节点执行语义的系统指纹 | 决定节点自身配置/逻辑是否变化 |
| input_fingerprint | 任务、工作流输入和上游输出的指纹 | 决定当前节点输入是否变化 |
| dependency_fingerprint | 入边、条件和上游结果关系的指纹 | 决定上游依赖是否变化 |

节点不维护人工递增版本号。节点 ID 作为身份单独校验；节点语义指纹不包含画布位置，也不包含当前实现中不参与执行的展示名称。

### 3.2 任务状态

现有任务状态保留，并新增 stale：

~~~text
pending   可领取的未执行任务
stale     最新任务版本的历史结果已落后于当前 workflow revision
running   已被某个 run 租约占用
completed 当前任务版本在当前 workflow revision 下已有成功结果
failed    最近一次执行失败且仍可观察/处理
abandoned 超过最大尝试次数或被永久放弃
~~~

领取条件扩展为：

~~~text
status = 'pending'
OR status = 'stale'
OR (status = 'running' AND lease_expires_at < now)
~~~

pending 优先级高于 stale。stale 不代表新的任务版本，只代表当前任务版本对应的历史结果已过时。

### 3.3 最新任务版本规则

同一个 task_key 的不同 task_version 是独立任务行。默认只让 set_at 最新的任务版本进入 stale：

~~~text
ORDER BY set_at DESC, id DESC
LIMIT 1
~~~

不能按版本字符串排序，避免 v10 与 v9 的字典序问题。

workflow revision 更新时：

- 最新任务版本为 completed，且其最近成功 run 使用旧 revision：改为 stale；
- 最新任务版本为 pending 或 running：不降级或覆盖当前状态；
- 最新任务版本已经使用当前 revision 成功：保持 completed；
- 较旧任务版本即使 completed，也保持 completed，不进入默认调度路径。

## 4. 状态和运行关系

~~~mermaid
stateDiagram-v2
    [*] --> pending
    pending --> running
    stale --> running
    running --> completed
    running --> failed
    running --> abandoned
    running --> pending: pending 目标任务停止/失败释放
    running --> stale: stale 目标任务停止/失败释放
    completed --> stale: 最新任务版本且 workflow revision 变化
    abandoned --> pending: 手动 reset
    failed --> pending: 可重试
~~~

实际释放时保留任务的目标状态：

- pending 任务执行失败，恢复为 pending；
- stale 任务执行失败，恢复为 stale；
- 超过尝试次数后转为 abandoned；
- 手动 reset 后根据当前 workflow revision 恢复为 pending 或 stale。

如果 workflow 在 run 执行期间发布了新 revision，则该 run 即使成功，也不能把任务稳定为当前结果；run 完成后需要重新核对当前 revision，必要时把任务标记为 stale。

## 5. 增量执行计划

每次增量执行前生成一份不可变的 IncrementalPlan。预览接口和实际执行使用同一个 planner，避免预览和执行采用不同判定逻辑。

### 5.1 计划输入

- 当前 workflow 定义快照、workflow_revision_no、content_hash；
- 精确的 task_key 和 task_version；
- 任务 payload 和 workflow input_data；
- 同一 workflow、profile、task key、task version 下最近成功的历史 run；
- 脚本、技能、后端和运行环境的解析结果及版本指纹；
- 节点和边的规范化图结构。

### 5.2 历史基准 run 选择

默认选择同一任务版本最近成功的完整 run 作为单一基准：

~~~text
workflow_key 相同
profile_key 相同
task_key 相同
task_version 相同
run.status = completed
run 的 definition snapshot 可解析
~~~

优先使用最新成功 run。若该 run 整体不可用，则回退到更早的成功 run；不在 v2、v3 两个 workflow run 之间逐节点拼接。单一基准策略用于避免混用不兼容的 workflow 版本。

若基准 run 中单个节点输出缺失或无效，不回退到其他 run 的同节点，而是从该节点重新执行，并向下游传播影响。

### 5.3 指纹组成

#### 节点语义指纹

包含：

- 节点 ID 和节点类型；
- 节点执行配置；
- 节点执行逻辑版本；
- 脚本 key、revision 和 content hash；
- skill 名称、revision 和 content hash；
- agent backend key 和可用的 backend/runtime 指纹；
- output handler/system role 等影响执行的配置。

不包含：

- 画布位置；
- 仅用于展示的名称；
- 不影响执行的 edge ID。

#### 入边和条件指纹

规范化记录：

- source node ID；
- target node ID；
- 条件字段、操作符和值；
- system role；
- 多条入边的排序后集合。

#### 输入指纹

第一版采用保守策略，包含：

- workflow input_data；
- 当前任务 payload；
- 上游节点输出的 canonical JSON hash；
- 与节点执行上下文有关的 profile/runtime 信息。

如果上游节点重新执行，即使输出碰巧相同，下游也默认重新执行；后续可根据稳定输出 hash 做更细粒度优化。

### 5.4 节点判定规则

节点只有在以下条件全部满足时才能 reuse：

~~~text
节点 ID 未变化
节点类型未变化
节点语义指纹相同
入边和条件语义相同
输入指纹相同
依赖指纹相同
历史节点状态为成功
output_json 存在且 hash 正确
关联 artifact 存在且仍可复用
task_version/profile/runtime 兼容
~~~

否则 execute，并记录稳定的 reason code：

~~~text
node_added
node_removed_or_missing
node_id_changed
node_type_changed
node_config_changed
node_logic_changed
resource_revision_changed
incoming_edge_changed
condition_changed
upstream_node_reexecuted
upstream_output_invalid
history_node_failed
output_missing
artifact_invalid
runtime_fingerprint_unknown
~~~

如果一个节点 execute，所有依赖它的下游节点默认 execute。如果节点只是复用了基准 run 的成功结果，并且指纹保持一致，则下游可以继续判断复用。

带有实时外部 MCP/能力读取的节点，如果不能获得稳定的外部输入或能力快照指纹，默认不可复用，原因记录为 runtime_fingerprint_unknown 或 external_dependency_unverifiable。

## 6. 运行时注入与缓存

### 6.1 现有持久化能力

当前已经持久化：

- workflow_node_runs.output_json：每个节点每次运行的结构化输出；
- workflow_runs.output_json：整个 run 的最终输出；
- workflow_artifacts.content：显式保存的 Markdown/HTML 内容。

这些数据目前是历史记录，不会被 executor 自动读取，因此需要增加复用分支。

### 6.2 复用流程

复用节点时：

1. 在当前 run 创建自己的 node run；
2. 读取基准 run 的 node run；
3. 校验来源状态、指纹、output_json hash 和 artifact 有效性；
4. 复制完整 output_json 到当前 node run；
5. 记录 execution_mode = reused 及来源字段；
6. 将输出放入当前 executor 的 outputs[node_id]；
7. 下游节点继续读取当前上下文，不感知来源 run。

复用的 output 必须包括完整 JSON，而不是只保存最终报告字段，以支持下游引用其中的部分或全部字段。

### 6.3 产物物化

- 数据库中有完整 content 的 artifact 可通过 artifact_id 只读引用；
- 历史 run 目录中的临时文件不能直接作为当前 run 的输入；
- 若下游节点需要文件路径，则将历史 artifact 内容复制、硬链接或安全物化到当前 run 目录；
- 当前 run 的物化映射要记录来源 artifact ID 和 content hash；
- 复用不会修改历史 artifact 的 content、来源 run 或历史 metadata；
- 新执行产生的 artifact 只写入当前 run。

## 7. 存储模型变更

### 7.1 workflow_runs

新增字段：

~~~text
workflow_revision_no
workflow_content_hash
task_version
execution_mode
source_run_id
execution_plan_json
~~~

definition_snapshot_json 继续保留，保证运行时使用创建 run 时的不可变 workflow 快照。

### 7.2 workflow_node_runs

新增字段：

~~~text
node_fingerprint
action
reuse_reason
source_run_id
source_node_id
source_node_fingerprint
artifact_ids_json
~~~

节点的数据库 status 继续表示执行结果状态；是否执行或复用由 execution_mode 单独表示，避免把 reused 和 completed 混成一个维度。

### 7.3 workflow_artifacts

建议新增：

~~~text
producer_node_id
producer_node_fingerprint
reuse_allowed
invalid_reason
workflow_run_artifacts(run_id, node_id, artifact_id, source_run_id, source_node_id)
~~~

is_current 继续用于当前结果展示，但不能作为复用唯一条件；历史 artifact 即使不是 current，只要来源、hash 和有效期校验通过，也可以被复用。

第一版不新建全局 node cache 表。成功的 workflow_node_runs 作为缓存源，当前 run 的 node run 作为复用结果和审计记录。

## 8. API 与页面

### 8.1 预览

新增：

~~~text
POST /workflows/{workflow_key}/run/preview
~~~

请求至少支持：

~~~json
{
  "task_key": "page:a",
  "task_version": "v3",
  "execution_mode": "incremental",
  "input": {}
}
~~~

返回：

- 目标 workflow revision；
- 目标 task version；
- 选中的 source run；
- reuse/execute 数量；
- 每个节点的动作、reason、来源 node run；
- 预计受影响的下游范围；
- 计划是否可执行。

### 8.2 运行

扩展现有运行接口，支持：

~~~text
execution_mode = normal / incremental / force_full
task_key
task_version
~~~

按钮默认行为：

~~~text
pending   → normal
stale     → incremental
completed + 当前 revision → 展示已有结果，允许 force_full
~~~

有 artifact 的列表项通过 artifact lineage 找到对应 workflow/task/run，并复用同一运行入口。没有任务队列的手动输入型 workflow 仍可直接运行，使用 input_data 参与计划。

### 8.3 详情

GET /workflow-runs/{run_id} 返回：

- workflow revision 和 task version；
- execution mode；
- source run；
- 增量计划；
- 每个 node run 的执行状态；
- executed/reused；
- 复用来源；
- 未复用原因；
- output hash 和 artifact 映射。

页面展示 stale 状态，并在运行详情中区分：

~~~text
已执行
已复用
未执行原因
~~~

## 9. 并发、一致性与失败处理

### 9.1 计划和 revision CAS

预览或运行开始时记录预期的 workflow_revision_no。真正创建 run 前再次检查当前 revision：

- 仍然一致：继续执行；
- 已变化：重新生成计划，不使用旧计划。

### 9.2 历史数据只读

历史 run、历史 node run 和历史 artifact 不做覆盖更新。当前 run 只写自己的记录，来源通过 ID 和 hash 关联。

### 9.3 复用前二次校验

计划生成和真正复用之间再次校验：

- source run 状态；
- source node 状态；
- output hash；
- artifact hash/有效期；
- workflow/profile/task version 关系。

校验失败时当前节点转换为 execute，并向下游传播，不使用失效数据。

### 9.4 任务租约

pending 和 stale 使用同一套数据库事务领取机制。一个 stale 任务只能被一个 run 获得有效 lease；并发 run 不能同时消费同一任务。

## 10. 验收场景映射

| 场景 | 预期 |
| --- | --- |
| 仅修改 c | 复用 a、b，执行 c、d |
| 修改 b | 复用 a，执行 b、c、d |
| 修改 d | 复用 a、b、c，只执行 d |
| 修改 task_version | 新版本完整执行，禁止复用旧任务版本 |
| 修改节点配置 | 当前节点及全部下游执行 |
| 只修改节点位置 | 保持节点指纹，允许复用 |
| 修改边或条件 | 重新计算受影响下游 |
| 新增节点 | 新节点及必要下游执行 |
| 删除节点 | 新图中不存在的节点不再创建 node run，下游按依赖重新判断 |
| 历史产物缺失 | 从缺失节点开始执行并向下游传播 |
| 同一 task_key 多版本 | 只有 set_at 最新版本可变 stale，旧版本保持 completed |
| v2/v3 历史 run，当前 v4 | 以最近可用成功 run 为单一基准，不跨 run 拼接 |
| stale 执行失败 | 任务保持 stale 或按尝试次数转 abandoned |
| 强制全量 | 所有节点 execute，不读取历史节点输出 |
| 并发 run | 互不覆盖 node run、output_json 和 artifact |
| 预览和实际运行 | 使用同一 planner，结果一致或在 revision 变化时重新计划 |

## 11. 实施顺序

1. 增加 stale 状态、最新 task version 按 set_at 计算和领取逻辑；
2. 为 run/node run 增加版本、指纹、来源和 execution mode 字段；
3. 实现 canonical fingerprint 和资源版本解析；
4. 实现历史 source run 选择与增量 planner；
5. 改造 executor，支持复用 output_json 和下游上下文注入；
6. 增加 artifact 有效性、来源校验和当前 run 物化；
7. 增加预览/运行 API；
8. 更新任务页、artifact 页和 run 详情页；
9. 按验收矩阵补充单元、集成、并发和 API 测试。

## 12. 兼容和迁移

- 旧 workflow run 没有 revision metadata 时，不能直接作为增量 source；需要补齐快照指纹，或保守地从根节点重新执行；
- 旧 node run 没有 fingerprint 时，不能直接复用；
- 旧 artifact 没有 lineage 时，只能在 workflow/profile/task version 和 content hash 可确认时复用；
- 旧 completed task 在 workflow revision 更新后按最新 set_at 规则转换为 stale；
- 迁移失败不删除历史 run、node run 或 artifact。

## 13. 设计结论

采用“整体 workflow revision + 自动节点指纹 + stale 任务状态 + 每次 run 独立记录”的方案：

- stale 负责让最新任务版本重新进入队列；
- planner 负责计算 DAG 影响范围；
- output_json 负责复用结构化节点结果；
- artifact lineage 和 hash 负责文件产物的一致性；
- 新 run 负责隔离当前结果和历史结果；
- 单一 source run 策略负责避免跨 workflow 版本混用不兼容产物。
