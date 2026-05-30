# wiki-manager 第三阶段设计

日期：2026-05-30

## 1. 目标

在第二阶段多后端同步的基础上，接入检索与问答能力，使用户可通过 CLI、API 和 MCP 工具查询知识库内容。扩展 BackendAdapter Protocol 以适配不同后端的检索特性，先实现 RagFlow 检索+问答适配器，验证端到端流程。

## 2. 范围

### 做

- BackendAdapter Protocol 扩展：新增 `retrieve()` 和 `ask()` 方法。
- RetrievalResult / AskResult 数据类。
- RagFlow 适配器实现 retrieve（`POST /api/v1/retrieval`）和 ask（Chat Assistant + `POST /api/v1/chat/completions`）。
- Chat Assistant 生命周期管理：自动创建、config_json 存储 chat_id、lazy 初始化。
- MockBackend retrieve/ask 降级实现。
- 服务层检索路由：默认主后端 + `--backend` 覆盖。
- API 端点：`GET /search`、`POST /ask`。
- CLI 命令：`wiki search`、`wiki ask`。
- MCP 服务：暴露 `search` 和 `ask` 只读工具。
- server.toml 新增 `default_backend` 和 `[mcp]` 配置段。

### 不做

- WeKnora、MaxKB 等其他真实后端检索适配（架构预留，但不实现）。
- Web UI。
- 异步检索或后台检索任务。
- 检索结果缓存。
- 流式问答（streaming）。
- 用户认证/权限控制。

## 3. BackendAdapter Protocol 扩展

### 数据类

```python
@dataclass
class RetrievalResult:
    chunk_id: str
    content: str
    document_name: str
    similarity: float
    dataset_id: str

@dataclass
class AskResult:
    answer: str
    chunks: list[RetrievalResult]
    session_id: str | None
```

### Protocol 新增方法

```python
class BackendAdapter(Protocol):
    # Phase 2 已有方法不变
    def create_kb(self, slug: str, name: str) -> str: ...
    def delete_kb(self, backend_kb_id: str) -> None: ...
    def upload(self, backend_kb_id: str, doc_slug: str,
               file_path: Path, filename: str) -> str: ...
    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None: ...
    def get_status(self, backend_kb_id: str,
                   backend_doc_id: str) -> BackendDocStatus: ...

    # Phase 3 新增
    def retrieve(self, backend_kb_id: str, question: str,
                 top_k: int = 6) -> list[RetrievalResult]: ...
    def ask(self, backend_kb_id: str, question: str,
            session_id: str | None = None) -> AskResult: ...
```

`retrieve()` 对指定知识库执行向量检索，返回匹配 chunk 列表。`ask()` 基于知识库执行问答，返回答案及引用 chunk，可选传入 `session_id` 实现多轮对话。

## 4. RagFlow 适配器

### retrieve() 实现

API 映射：`POST /api/v1/retrieval`

```python
def retrieve(self, backend_kb_id: str, question: str,
             top_k: int = 6) -> list[RetrievalResult]:
    response = self._request(
        "POST",
        f"{self.base_url}/api/v1/retrieval",
        json={
            "question": question,
            "dataset_ids": [backend_kb_id],
            "top_k": top_k,
        },
    )
    self._raise(response)
    chunks = response.json()["data"]["chunks"]
    return [
        RetrievalResult(
            chunk_id=c["id"],
            content=c["content"],
            document_name=c.get("document_keyword", ""),
            similarity=c.get("similarity", 0.0),
            dataset_id=c.get("dataset_id", ""),
        )
        for c in chunks
    ]
```

### ask() 与 Chat Assistant 生命周期

RagFlow 问答需要先创建 Chat Assistant 并绑定 dataset。生命周期策略：

1. **chat_id 传入**：`ask()` 接收 `chat_id: str | None` 参数。由服务层从 `backend_targets.config_json` 读取并传入。
2. **自动创建**：如果 `chat_id` 为 None，adapter 调用 `POST /api/v1/chats` 创建 Chat Assistant，绑定该 dataset，返回新的 chat_id。服务层负责将 chat_id 持久化到 config_json。
3. **会话管理**：`session_id` 由调用方传入（多轮对话）。不传时自动创建一次性 session。

**职责边界**：adapter 只负责 API 调用和内存缓存，不直接访问数据库。chat_id 的持久化由服务层通过 storage 完成。

```python
def ask(self, backend_kb_id: str, question: str,
        chat_id: str | None = None,
        session_id: str | None = None) -> tuple[AskResult, str]:
    """Returns (AskResult, chat_id). chat_id may be newly created."""
    if chat_id is None:
        chat_id = self._create_chat_assistant(backend_kb_id)

    if session_id is None:
        session_id = self._create_session(chat_id)

    response = self._request(
        "POST",
        f"{self.base_url}/api/v1/chat/completions",
        json={
            "chat_id": chat_id,
            "session_id": session_id,
            "question": question,
            "stream": False,
        },
    )
    self._raise(response)
    data = response.json()["data"]
    chunks = self._extract_chunks(data)
    result = AskResult(
        answer=data["answer"],
        chunks=chunks,
        session_id=session_id,
    )
    return result, chat_id
```

`ask()` 返回 `(AskResult, chat_id)` 元组。chat_id 可能是新创建的，服务层负责与 config_json 中的值比较，不同则持久化。

### 辅助方法

**Chat Assistant 创建**：

```python
def _create_chat_assistant(self, backend_kb_id: str) -> str:
    response = self._request(
        "POST",
        f"{self.base_url}/api/v1/chats",
        json={
            "name": f"wiki-mgr-{backend_kb_id[:8]}",
            "dataset_ids": [backend_kb_id],
            "llm": {"model_name": "default"},
        },
    )
    self._raise(response)
    return response.json()["data"]["id"]
```

**Session 创建**：

```python
def _create_session(self, chat_id: str) -> str:
    response = self._request(
        "POST",
        f"{self.base_url}/api/v1/chats/{chat_id}/sessions",
        json={"name": "wiki-session"},
    )
    self._raise(response)
    return response.json()["data"]["id"]
```

**Chunk 提取**：`_extract_chunks(data)` 从 RagFlow 响应的引用信息中解析为 `list[RetrievalResult]`。

**chat_id 持久化**：服务层调用 `ask()` 时从 config_json 读取 chat_id 传入。如果返回的 chat_id 与传入值不同（新创建），服务层负责更新 config_json。adapter 不直接访问存储层。

### API 映射汇总

| Adapter 方法 | RagFlow API | 说明 |
|---|---|---|
| `retrieve` | `POST /api/v1/retrieval` | 向量检索，传入 dataset_ids |
| `ask` | `POST /api/v1/chat/completions` | LLM 问答，需 chat_id + session_id |
| `_create_chat_assistant` | `POST /api/v1/chats` | 创建 Chat Assistant 绑定 dataset |
| `_create_session` | `POST /api/v1/chats/{id}/sessions` | 创建对话 session |

## 5. MockBackend 降级

```python
def retrieve(self, backend_kb_id: str, question: str,
             top_k: int = 6) -> list[RetrievalResult]:
    return []

def ask(self, backend_kb_id: str, question: str,
        chat_id: str | None = None,
        session_id: str | None = None) -> tuple[AskResult, str]:
    return AskResult(
        answer="mock backend does not support Q&A",
        chunks=[],
        session_id=None,
    ), ""
```

Mock 后端不支持检索和问答，返回空结果或提示消息，不抛异常。

## 6. 检索路由

### 策略

1. 用户通过 `--backend` 指定后端 → 直接路由到该后端。
2. 未指定 → 查 `server.toml` 中 `default_backend` 配置。
3. 无配置 → 取该 KB 第一个 `status=active` 且 adapter 支持 retrieve 的 target。
4. 找不到 → 抛异常，提示无可检索后端。

### 服务层实现

```python
def search(self, kb_slug: str, question: str, *,
           backend_slug: str | None = None,
           top_k: int = 6) -> list[RetrievalResult]:
    target = self._resolve_retrieval_target(kb_slug, backend_slug)
    adapter = self._get_adapter(target.slug)
    return adapter.retrieve(target.backend_kb_id, question, top_k)

def ask(self, kb_slug: str, question: str, *,
        backend_slug: str | None = None,
        session_id: str | None = None) -> AskResult:
    target = self._resolve_retrieval_target(kb_slug, backend_slug)
    adapter = self._get_adapter(target.slug)
    chat_id = target.config_json.get("chat_id") if target.config_json else None
    result, new_chat_id = adapter.ask(
        target.backend_kb_id, question, chat_id=chat_id, session_id=session_id,
    )
    if new_chat_id != chat_id:
        self._save_chat_id(target, new_chat_id)
    return result
```

`_resolve_retrieval_target()` 实现上述 4 步路由逻辑。

### 配置

```toml
default_backend = "ragflow"
```

放在 server.toml 顶层，未配置时不报错，使用 fallback 逻辑（步骤 3）。

## 7. API 端点

### 检索

```
GET /search?kb=<slug>&q=<question>&backend=<slug>&top_k=6
```

响应：

```json
{
  "results": [
    {
      "chunk_id": "xxx",
      "content": "...",
      "document_name": "接口说明.md",
      "similarity": 0.89,
      "dataset_id": "ds_xxx"
    }
  ]
}
```

### 问答

```
POST /ask
Content-Type: application/json

{
  "kb": "<slug>",
  "question": "...",
  "backend": "<slug>",
  "session_id": null
}
```

响应：

```json
{
  "answer": "...",
  "chunks": [...],
  "session_id": "sess_xxx"
}
```

客户端可用返回的 `session_id` 发起多轮对话。

## 8. CLI 命令

```bash
wiki search <question> --kb <slug> [--backend ragflow] [--top-k 6]
wiki ask <question> --kb <slug> [--backend ragflow] [--session <id>]
```

`search` 输出匹配 chunk 列表（序号、文档名、相似度、内容摘要）。`ask` 输出答案和引用来源。多轮对话时 `--session` 传入上一轮返回的 session_id。

## 9. MCP 服务

### 配置

```toml
[mcp]
enabled = true
transport = "stdio"
```

`transport` 可选 `stdio` 或 `sse`。集成在 wiki-manager server 进程中。

### 工具定义

| 工具 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `search` | `kb_slug`, `question`, `backend?`, `top_k?` | `list[RetrievalResult]` | 检索知识库 chunk |
| `ask` | `kb_slug`, `question`, `backend?`, `session_id?` | `AskResult` | 基于知识库问答 |

两个工具均为纯只读，不修改任何数据。MCP 客户端（如 Claude Desktop、Cursor）可通过这两个工具查询已配置的知识库。

### MCP 技术选型

使用 `mcp` Python SDK 实现 MCP server，在 FastAPI lifespan 中根据配置启动。

## 10. 数据模型变更

无新增表。复用现有结构：

- `backend_targets.config_json`：新增 `"chat_id"` 字段，lazy 写入。
- `server.toml`：新增 `default_backend` 顶层字段和 `[mcp]` 段。

## 11. 错误处理

| 场景 | 处理 |
|------|------|
| 后端不支持检索（如 mock） | `retrieve()` 返回空列表，`ask()` 返回提示消息 |
| Chat Assistant 创建失败 | `ask()` 抛异常，API 返回 502，提示检查后端 LLM 配置 |
| RagFlow 检索无结果 | `retrieve()` 返回空列表，非错误 |
| 后端不可达 | 连接异常 → API 返回 502 |
| 无可用检索后端 | `_resolve_retrieval_target()` 抛异常，API 返回 404 |
| session_id 无效 | RagFlow 返回错误 → 抛异常，建议不传 session 重试 |

## 12. 测试策略

### 单元测试（新增）

- `RetrievalResult` / `AskResult` 数据类构造和序列化。
- `_resolve_retrieval_target()` 路由逻辑：指定后端、默认后端、无后端、不可用后端。
- RagFlow adapter `retrieve()`：用 respx mock `/api/v1/retrieval`。
- RagFlow adapter `ask()`：mock chat 创建 + session 创建 + completions。
- Chat Assistant lazy 创建：首次 ask 触发，后续复用。
- MockBackend retrieve/ask 降级行为。

### API 测试（扩展）

- `GET /search` 正常检索、指定 backend、无可用后端。
- `POST /ask` 正常问答、多轮 session、后端不支持。
- MCP 工具定义符合规范。

### 端到端烟测（@pytest.mark.ragflow）

在真实 RagFlow 实例上验证：

1. 上传文档 → 等解析完成 → `search` 能检索到相关 chunk。
2. `ask` 能返回带引用的答案。
3. 多轮对话 session 正常工作。
4. 指定 `--backend` 切换后端。

## 13. 成功标准

第三阶段完成后，应能完成以下流程：

1. 管理员在 server.toml 中配置 RagFlow 后端和 `default_backend`。
2. 已有文档同步完成后，`wiki search` 可检索到相关 chunk。
3. `wiki ask` 基于知识库内容回答问题并附引用。
4. 多轮对话通过 `--session` 参数正常工作。
5. MCP 客户端可通过 `search` 和 `ask` 工具查询知识库。
6. `--backend` 参数可切换检索目标后端。
7. 不支持检索的后端优雅降级，不影响其他后端。
