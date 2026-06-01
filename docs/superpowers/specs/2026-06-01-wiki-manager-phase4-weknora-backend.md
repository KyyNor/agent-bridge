# wiki-manager 第四阶段 Weknora 后端设计

日期：2026-06-01

## 1. 目标

新增 Weknora 真实后端，使 wiki-manager 能以与 RagFlow 相同的 BackendAdapter 契约完成知识库创建、文档同步、状态查询、检索和问答。Weknora 已在本机 80 端口运行，本阶段以 HTTP API 为唯一自动化路径，不在适配器或验收脚本中调用 `weknora` CLI。

## 2. 范围

### 做

- 通过 Weknora HTTP API 注册 wiki-manager 专用用户，拿到 tenant API key。
- 将注册信息、Weknora URL、tenant ID、模型 ID 和 API key 摘要写到 `/Users/kyynor/DockerData/wiki-manager-weknora-env.md`，该文件不进入 git。
- 从 `/Users/kyynor/.config/gowiki/config.yaml` 读取现有 LLM 和 Embedding 配置，并通过 Weknora `/api/v1/models` 创建或复用模型。
- 新增 `WeknoraBackend`，实现现有 `BackendAdapter` Protocol。
- 新增 `backend_type = "weknora"` 注册表支持。
- 支持 `server.toml` 配置 Weknora 后端。
- 新增 Weknora 单元测试和 live integration 测试 marker。
- 验收一个真实端到端流程：注册、模型配置、创建 KB、上传文档、等待解析、检索、问答、清理。

### 不做

- 不使用 `weknora` CLI 作为实现路径。
- 不新增 Web UI。
- 不改变 Phase 3 已有 CLI/API/MCP 用户接口。
- 不引入后台 worker 或异步任务队列。
- 不做多租户权限同步，只使用 Weknora tenant API key 作为后端凭据。
- 不把模型供应商 API key 写入仓库内配置或测试文件。

## 3. 运行时配置

`server.toml` 新增后端段：

```toml
[backends.weknora]
backend_type = "weknora"
base_url = "http://localhost"
api_key = "sk-..."
timeout = 120
embedding_model_id = "..."
summary_model_id = "..."
```

`api_key` 是 Weknora tenant API key，用于请求头 `X-API-Key`。LLM/Embedding 上游 key 不写入 wiki-manager 配置；它们只在 Weknora 模型配置中保存。

## 4. 注册与模型配置

### 注册

注册脚本或验收脚本直接调用：

| 目的 | API |
|---|---|
| 注册用户 | `POST /api/v1/auth/register` |
| 登录验证 | `POST /api/v1/auth/login` |
| 当前用户验证 | `GET /api/v1/auth/me` |

注册成功后记录：

- `base_url`
- `username`
- `email`
- `tenant.id`
- `tenant.name`
- `tenant.api_key`
- 创建时间
- 使用的模型名称和模型 ID

凭据落盘位置固定为：

```text
/Users/kyynor/DockerData/wiki-manager-weknora-env.md
```

该文件包含明文 tenant API key，应设置为仅当前用户可读写。

### 模型配置

从 `/Users/kyynor/.config/gowiki/config.yaml` 读取：

- chat model：DeepSeek，`deepseek-v4-flash`
- embedding model：SiliconFlow，`BAAI/bge-large-zh-v1.5`

通过 Weknora 模型 API 创建或复用：

| 模型类型 | Weknora type | API |
|---|---|---|
| 对话模型 | `KnowledgeQA` | `GET /api/v1/models` 后按 name/type/provider 查找，不存在则 `POST /api/v1/models` |
| 嵌入模型 | `Embedding` | `GET /api/v1/models` 后按 name/type/provider 查找，不存在则 `POST /api/v1/models` |

创建 KB 时显式传入：

- `embedding_model_id`
- `summary_model_id`

如果 Weknora 要求 KB 初始化模型配置，则在 KB 创建后调用：

- `POST /api/v1/initialization/initialize/:kb_id`

## 5. BackendAdapter 映射

### API 映射

| Adapter 方法 | Weknora API | 说明 |
|---|---|---|
| `create_kb(slug, name)` | `POST /api/v1/knowledge-bases` | 创建 document 类型 KB，返回 `data.id` |
| `delete_kb(backend_kb_id)` | `DELETE /api/v1/knowledge-bases/:id` | 删除 Weknora KB |
| `upload(backend_kb_id, doc_slug, file_path, filename)` | `POST /api/v1/knowledge-bases/:id/knowledge/file` | multipart 上传文件，返回 `data.id` |
| `delete(backend_kb_id, backend_doc_id)` | `DELETE /api/v1/knowledge/:id` | 删除单条知识 |
| `get_status(backend_kb_id, backend_doc_id)` | `GET /api/v1/knowledge/:id` | 读取 `parse_status`、错误和 chunk 信息 |
| `retrieve(backend_kb_id, question, top_k)` | `POST /api/v1/knowledge-search` | 检索 chunk，client 侧裁剪 top_k |
| `ask(backend_kb_id, question, session_id)` | `POST /api/v1/sessions` + `POST /api/v1/knowledge-chat/:session_id` | 解析 SSE，聚合 answer 和 references |

### 状态映射

Weknora `parse_status` 到 wiki-manager `BackendDocStatus.status`：

| Weknora | wiki-manager |
|---|---|
| `pending` | `pending` |
| `processing` | `parsing` |
| `finalizing` | `parsing` |
| `completed` | `completed` |
| `failed` | `failed` |
| `cancelled` | `failed` |
| 404 | `not_found` |

`error_message` 原样映射到 `BackendDocStatus.error_message`。`chunk_count` 优先从知识详情或 KB 统计中读取；如果接口不返回单文档 chunk 数，允许为 `None`，不阻塞同步状态。

### 检索结果映射

`POST /knowledge-search` 返回的 `data[]` 映射为：

```python
RetrievalResult(
    chunk_id=item["id"],
    content=item["content"],
    document_name=item.get("knowledge_title") or item.get("knowledge_filename") or "",
    similarity=float(item.get("score") or 0.0),
    dataset_id=item.get("knowledge_base_id") or backend_kb_id,
)
```

如果 API 返回超过 `top_k` 条，由适配器裁剪到 `top_k`。

### 问答结果映射

问答使用 Weknora SSE：

1. 如果未传 `session_id`，先 `POST /api/v1/sessions` 创建 session。
2. 调用 `POST /api/v1/knowledge-chat/:session_id`，请求体包含：

```json
{
  "query": "question",
  "knowledge_base_ids": ["kb-id"],
  "disable_title": true,
  "channel": "api"
}
```

3. 解析 `event: message` 的 `data` JSON：
   - `response_type == "references"`：提取 `knowledge_references` 为 chunks。
   - `response_type == "answer"`：拼接 `content`，直到 `done == true`。
   - `response_type == "error"`：抛出 RuntimeError。

返回：

```python
AskResult(answer=answer, chunks=chunks, session_id=session_id)
```

由于 Weknora session 本身就是会话容器，`WeknoraBackend` 不需要 RagFlow 的 backend-level chat assistant。适配器仍满足现有 Protocol 的 tuple 返回值，但第二个值返回传入的 `chat_id` 或空字符串，避免服务层把 Weknora session_id 写入 `backend_targets.config_json.chat_id`。多轮对话只通过 `AskResult.session_id` 暴露给调用方，并由下一次请求显式传回。

## 6. 代码结构

预计变更：

- `src/wiki_manager/weknora_backend.py`：新增 HTTP API adapter。
- `src/wiki_manager/registry.py`：注册 `backend_type = "weknora"`。
- `tests/test_weknora_backend.py`：HTTP 单元测试，覆盖请求体、状态映射、SSE 解析、错误处理。
- `tests/test_weknora_integration.py`：live Weknora 验收测试，使用本机 `http://localhost`。
- `pyproject.toml`：新增 `weknora` pytest marker。
- `docs/superpowers/specs/2026-06-01-wiki-manager-phase4-weknora-backend.md`：本设计文档。

不修改 `BackendAdapter` Protocol；Weknora 使用 Phase 3 已有方法集即可。

## 7. 错误处理

`WeknoraBackend` 统一处理：

- HTTP 非 2xx：抛出 `RuntimeError("Weknora API error ...")`。
- JSON 响应中 `success == false`：抛出包含 `message` 或 `error` 的 RuntimeError。
- SSE 中 `response_type == "error"`：抛出 RuntimeError。
- 请求超时：沿用 `BackendConfig.timeout`。
- 删除 KB/doc 时 404 可视为幂等成功，避免清理阶段失败阻塞验收。

## 8. 验收标准

### 单元测试

必须通过：

```bash
uv run pytest tests/test_weknora_backend.py -v --tb=short
uv run pytest tests/test_registry.py tests/test_config_backends.py -v --tb=short
uv run pytest -v -m "not ragflow and not weknora" --tb=short
```

### Live integration

在 Weknora 已运行于 80 端口时执行：

```bash
uv run pytest tests/test_weknora_integration.py -v -m weknora --tb=short
```

测试步骤：

1. 读取 `/Users/kyynor/.config/gowiki/config.yaml`，不打印 secret。
2. 注册或复用 wiki-manager 专用用户。
3. 创建或复用 DeepSeek chat 模型和 SiliconFlow embedding 模型。
4. 写入 `/Users/kyynor/DockerData/wiki-manager-weknora-env.md`。
5. 创建临时 KB。
6. 上传一份 Markdown 测试文档。
7. 轮询到 `completed`。
8. `retrieve()` 能返回包含测试事实的 chunk。
9. `ask()` 能返回非空答案和 session_id。
10. 删除文档和临时 KB。

## 9. 实施顺序

1. 写 `tests/test_weknora_backend.py`，先覆盖 HTTP 映射和 SSE 解析。
2. 实现 `src/wiki_manager/weknora_backend.py`。
3. 注册 `backend_type = "weknora"`。
4. 写 Weknora live integration 测试和 bootstrap helper。
5. 跑本地单元测试。
6. 对 80 端口 Weknora 跑 live 验收。
7. 将 Weknora backend 配置写入 wiki-manager 本地 `server.toml`。
