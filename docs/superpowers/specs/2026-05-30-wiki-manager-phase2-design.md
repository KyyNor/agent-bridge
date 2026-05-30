# wiki-manager 第二阶段设计

日期：2026-05-30

## 1. 目标

在第一阶段入库账本闭环的基础上，接入 RagFlow 真实知识库后端，验证适配器架构和多后端并行同步模型。增强文档状态查询能力，使同步状态可见到后端的解析进度、chunk 数量等细节。验证 mock → RagFlow 迁移流程。

## 2. 范围

### 做

- 从 MockBackend 提炼 BackendAdapter 抽象接口。
- 实现 RagFlowBackend 适配器，调用 RagFlow HTTP API。
- 多后端自动绑定：知识库创建时自动绑定所有配置后端，无需显式声明。
- 文档状态增强：显示后端原始状态、chunk 数量、解析进度。
- mock → RagFlow 迁移验证：通过配置变更和服务重启自动触发。
- 失败重试：沿用第一阶段状态机，通过 `wiki sync` 手动重试。
- server.toml 管理 RagFlow 连接信息和凭据。
- `--backend` 参数过滤状态查询和同步目标。

### 不做

- 知识库检索、问答。
- MCP 服务。
- WeKnora、MaxKB 等其他真实后端适配（数据模型和架构预留，但不实现）。
- 异步任务队列或后台 worker。
- Web UI。
- 强身份认证。

## 3. BackendAdapter 抽象接口

从现有 MockBackend 提炼 Protocol：

```python
class BackendAdapter(Protocol):
    def create_kb(self, slug: str, name: str) -> str:
        """创建后端知识库，返回后端 KB ID"""

    def delete_kb(self, backend_kb_id: str) -> None:
        """删除后端知识库"""

    def upload(self, backend_kb_id: str, doc_slug: str,
               file_path: Path, filename: str) -> str:
        """上传文档，返回后端文档 ID"""

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        """删除后端文档"""

    def get_status(self, backend_kb_id: str,
                   backend_doc_id: str) -> BackendDocStatus:
        """查询后端文档状态"""
```

```python
@dataclass
class BackendDocStatus:
    status: str              # pending / parsing / completed / failed
    chunk_count: int | None
    progress: float | None   # 0.0 ~ 1.0
    error_message: str | None
```

MockBackend 显式满足该 Protocol，行为不变。RagFlowBackend 调用 RagFlow HTTP API 实现每个方法。

适配器类注册：

```python
ADAPTER_CLASSES: dict[str, type[BackendAdapter]] = {
    "mock": MockBackend,
    "ragflow": RagFlowBackend,
}
```

## 4. 多后端自动绑定

### 配置

server.toml 支持多个后端段落：

```toml
admins = ["root"]
host = "127.0.0.1"
port = 8765

[backends.ragflow]
backend_type = "ragflow"
base_url = "http://127.0.0.1:9380"
api_key = "ragflow-xxxxxxxx"
timeout = 120

[backends.mock]
backend_type = "mock"
```

每个 `[backends.xxx]` 段的 key 即 backend slug。服务启动时扫描所有段落，按 `backend_type` 实例化 Adapter，构建运行时注册表。

### 知识库创建

创建 KB 时自动为所有已配置后端调用 `create_kb()`，返回的 `backend_kb_id` 写入 `backend_targets` 表。如果某个后端 `create_kb()` 失败，该 target 标记为 `inactive`，不阻断其他后端。后续可通过 `wiki sync` 重试。

### 服务启动时自动对齐

服务启动时检查每个已有 KB 是否有对应所有配置后端的 target 记录：

- 缺失的：自动创建 target 并调用 `create_kb()`。
- 对每个新建 target，遍历该 KB 下所有 `sync_state.status = 'synced'` 的文档，为新 target 创建 `pending` sync job。等价于隐式迁移。
- 配置中已移除的后端：对应 target 标记为 `inactive`，不再同步，保留历史状态。

### mock 后端变更

mock 不再是硬编码的默认后端，变为 server.toml 中的显式配置段落。没有 `[backends.*]` 段时注册表为空，不自动创建任何 target。如果用户希望保持 mock 行为，在 server.toml 中显式添加 `[backends.mock]` 段。

## 5. RagFlow 适配器

### API 映射

| Adapter 方法 | RagFlow API | 说明 |
|---|---|---|
| `create_kb` | `POST /api/v1/datasets` | slug 映射为 dataset name |
| `delete_kb` | `DELETE /api/v1/datasets/{id}` | |
| `upload` | `POST /api/v1/datasets/{id}/documents` | 上传原始文件 |
| `delete` | `DELETE /api/v1/documents/{id}` | |
| `get_status` | `GET /api/v1/documents/{id}` | 获取解析状态 |

### 语义

- **上传**：`upload()` 提交文件后立即返回 RagFlow document ID。RagFlow 后台异步解析和向量化，`upload()` 不等待完成。
- **更新**：先 `delete` 旧文档再 `upload` 新版本，与 mock 后端语义一致。RagFlow 不支持原位更新文件内容。
- **状态轮询**：`get_status()` 只在用户显式查询或 sync 执行前调用，不启动后台定时轮询。

### 配置段

```toml
[backends.ragflow]
backend_type = "ragflow"
base_url = "http://127.0.0.1:9380"
api_key = "ragflow-xxxxxxxx"
timeout = 120
```

## 6. 数据模型变更

Phase 1 已有字段与 Phase 2 的对照：

### backend_targets 表

Phase 1 已有：`id`、`kb_id`、`slug`、`backend_type`、`config_json`、`status`、`created_at`、`updated_at`。

Phase 2 新增：

```sql
ALTER TABLE backend_targets ADD COLUMN backend_kb_id TEXT;
```

现有字段语义调整：

- `slug`：语义等价于 `backend_slug`，对应 `[backends.xxx]` 的 key。Phase 1 默认值 `"mock"`，Phase 2 由配置驱动。
- `status`：Phase 1 默认值已是 `"active"`。Phase 2 新增 `"inactive"` 语义，用于配置中已移除的后端。
- `config_json`：保留，可用于存储后端特有的运行时配置。

### sync_states 表

Phase 1 已有：`doc_id`、`kb_id`、`backend_slug`、`backend_doc_id`、`status`、`updated_at`（联合主键 `doc_id, kb_id, backend_slug`）。

Phase 2 新增：

```sql
ALTER TABLE sync_states ADD COLUMN backend_status TEXT;
ALTER TABLE sync_states ADD COLUMN chunk_count INTEGER;
ALTER TABLE sync_states ADD COLUMN progress REAL;
ALTER TABLE sync_states ADD COLUMN backend_error TEXT;
```

- `backend_status`：后端原始状态字符串（如 `"parsing"`、`"completed"`）。
- `chunk_count`、`progress`：`get_status()` 查询时更新。
- `backend_error`：同步失败时记录错误摘要。
- `backend_doc_id`：Phase 1 已有，Phase 2 开始真正写入后端返回的文档 ID。

### sync_jobs 表

Phase 1 已有 `backend_slug` 和 `error` 列，Phase 2 无需新增字段。同步失败时 `error` 列记录后端返回的错误摘要，与 `sync_states.backend_error` 冗余但各自服务于不同维度：`sync_jobs.error` 是任务级错误，`sync_states.backend_error` 是文档×后端级别的持久化错误。

### schema 升级

`POST /admin/init` 检测新列是否存在，不存在时执行 ALTER TABLE。从 Phase 1 schema 升级到 Phase 2，保持旧数据完整。

### auto-alignment 触发时机

多后端自动对齐在服务启动时（FastAPI lifespan `startup` 事件）执行，不依赖 `/admin/init`。`/admin/init` 只负责 schema 创建和升级。

## 7. 迁移流程

迁移不引入新命令，而是配置变更后的自然同步。

**mock → RagFlow 迁移步骤**：

1. 编辑 server.toml，加入 `[backends.ragflow]` 段（可保留或移除 `[backends.mock]`）。
2. `wiki server stop` → `wiki server start`。
3. 服务启动时自动为所有已有 KB 在 RagFlow 中创建 dataset，并为所有已入库文档创建 pending sync job。
4. 执行 `wiki sync`，逐个上传文档到 RagFlow。
5. `wiki doc <slug>` 查看新后端状态。

**验证标准**：迁移完成后，`wiki doc <slug>` 显示新后端 `sync_state.status = synced`，`chunk_count` 和 `backend_status` 可见。旧后端状态保留在账本中可查。

## 8. CLI 和 API 变更

### CLI

新增 `--backend` 参数：

```bash
wiki doc <slug>                        # 所有后端状态
wiki doc <slug> --backend ragflow      # 指定后端

wiki status                            # 所有后端
wiki status --backend ragflow          # 指定后端

wiki docs --kb <slug>                  # 所有后端
wiki docs --kb <slug> --backend ragflow # 指定后端

wiki sync                              # 所有后端
wiki sync --backend ragflow            # 指定后端
wiki sync --all                        # 管理员消费全部
```

`add`、`update`、`delete`、`purge` 不变，自动同步到所有活跃 target。

### API

现有端点增加 `backend` query parameter：

```text
GET  /docs?kb=<slug>&backend=ragflow
GET  /docs/{doc_slug}?backend=ragflow
GET  /status?backend=ragflow
POST /sync?backend=ragflow
```

新增端点：

```text
GET /backends
```

返回当前配置的所有后端：

```json
[
  {"slug": "ragflow", "type": "ragflow", "status": "active"},
  {"slug": "mock", "type": "mock", "status": "active"}
]
```

### 文档详情响应增强

```json
{
  "slug": "接口说明",
  "title": "接口说明",
  "current_version": 2,
  "sync_states": [
    {
      "backend_slug": "ragflow",
      "status": "synced",
      "backend_status": "completed",
      "chunk_count": 23,
      "progress": 1.0
    },
    {
      "backend_slug": "mock",
      "status": "synced",
      "backend_status": null,
      "chunk_count": null,
      "progress": null
    }
  ]
}
```

## 9. 错误处理

### 通用错误

沿用第一阶段状态机：适配器调用失败时 sync job → `failed`，sync_state → `sync_failed`（入库）或 `delete_failed`（删除），错误信息写入 `backend_error`。通过 `wiki sync` 手动重试。

### RagFlow 特有场景

| 场景 | 处理 |
|---|---|
| API Key 无效 | sync job → `failed`，记录 401 信息，管理员修正 server.toml 并重启 |
| 知识库被外部删除 | `get_status()` 返回 404 → `sync_failed`，重试时先 `create_kb()` 重建 |
| 文档解析失败 | `get_status()` 返回 failed → 更新 `backend_status` 和 `backend_error` |
| 上传超时 | 按 `timeout` 配置抛异常，sync job → `failed` |
| 后端 KB 创建失败 | target 标记 `inactive`，该后端暂停，不影响其他后端 |

### 重试策略

不引入自动指数退避。`failed` 的 job 通过 `wiki sync` 手动重试。

## 10. 测试策略

### 单元测试（新增）

- BackendAdapter Protocol 符合性。
- RagFlow 适配器：用 respx mock HTTP 调用，覆盖每个方法的成功和失败路径。
- 多后端注册表：server.toml 解析、适配器实例化、缺失字段报错。
- 服务启动时自动补建 target 的逻辑。

### 存储测试（扩展）

- `backend_targets` 新字段读写。
- `sync_states` 新字段读写。
- schema 升级：从 Phase 1 ALTER TABLE 到 Phase 2，验证旧数据不丢失。

### 应用服务测试（扩展）

- 创建 KB 时自动创建所有后端 target。
- 添加文档时为每个 target 创建 sync job。
- `--backend` 过滤逻辑。
- 服务启动时为新后端补建 target 和 pending jobs。
- 后端不可达时 target 标记 inactive。
- `get_status()` 按需刷新的时机。

### API 测试（扩展）

- `GET /backends` 返回正确列表。
- `--backend` query parameter 过滤。
- 后端创建 KB 失败时返回合适错误码。

### 端到端烟测（标记 @pytest.mark.ragflow）

在真实 RagFlow 实例上验证：

1. 配置 ragflow 后端，启动服务。
2. 创建 KB → 验证 RagFlow 中出现对应 dataset。
3. 添加文档 → 验证 RagFlow 中文档上传并开始解析。
4. `wiki doc` 显示解析状态和 chunk 数量。
5. 更新文档 → 验证 RagFlow 中旧文档被替换。
6. 删除文档 → 验证 RagFlow 中文档被移除。
7. 从 mock 迁移 → 重启后新后端自动补建 target 和 pending jobs。
8. `wiki sync` 完成迁移 → 所有文档在新后端中状态正确。

## 11. 成功标准

第二阶段完成后，应能在部门 wiki 虚拟机上完成以下流程：

1. 管理员在 server.toml 中配置 RagFlow 后端。
2. 重启服务，已有 KB 自动在 RagFlow 中创建 dataset。
3. 执行 `wiki sync`，已有文档自动上传到 RagFlow。
4. 用户通过 `wiki doc <slug>` 查看 RagFlow 解析进度和 chunk 数量。
5. 新添加的文档自动同步到所有配置后端。
6. 更新文档时 RagFlow 中旧文档被替换为新版本。
7. 删除文档时 RagFlow 中对应文档被移除。
8. 同步失败的任务可通过 `wiki status` 查看，通过 `wiki sync` 重试。
9. 管理员新增后端配置后，重启服务即可触发自动迁移。
