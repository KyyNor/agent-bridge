# wiki-manager 第一阶段设计

日期：2026-05-29

## 1. 项目定义

`wiki-manager` 是企业内部知识库的胶水层和运维控制面。它维护“哪些知识由谁、以什么原始材料、同步到了哪些知识库后端”的统一账本，并提供后端无关的运行维护能力。

本工具不直接承担复杂文档解析、切片、向量化、RAG 推理等知识库后端职责。第一阶段面向传统文档入库管理：`docx`、`xlsx/xls`、`pdf` 等原始文件由本工具归档保存，记录其归属知识库、版本、队列、权限、同步状态和后端文档 ID；实际文档处理仍由知识库后端完成。

长期目标包括：

- 统一知识入口：未来支持 API 文档、URL、代码仓库、数据库说明等来源，并可转换成统一 Markdown。
- 知识维护：支持直接入库和计划入库，计划入库用于夜间批处理，避免白天占用模型资源。
- 后端无关查询：统一提供知识库列表、检索、问答等接口，底层可切换 RagFlow、WeKnora、MaxKB 等后端。
- 同步与迁移：所有经本工具入库的知识保留本地存档和关系记录，可重放到新后端，也可同步删除。
- MCP 服务：为大模型暴露受控的知识库检索能力。
- 多后端混合：同一批知识可同步到多个后端，查询时允许用户选择后端或按策略组合使用。

## 2. 第一阶段范围

第一阶段聚焦“入库账本闭环”，不做查询和 MCP：

- 支持本工具内的逻辑知识库。
- 支持管理员和普通用户两类全局角色。
- 支持知识库内 `viewer`、`contributor`、`admin` 三档权限。
- 支持添加、更新、软删除、硬清理传统文档。
- 支持同一个原始文档关联多个逻辑知识库。
- 支持不可变版本模型，更新文档时保留历史版本。
- 支持立即同步和计划同步。
- 支持 SQLite 账本和原始文件归档。
- 支持本机 HTTP 服务统一管理共享数据。
- 支持默认 `mock` 后端适配器，用于验证同步状态机。
- 数据模型预留多个 backend target，但第一阶段 CLI/API 只开放默认 `mock`。

明确不做：

- API 文档生成和 Markdown 转换。
- 真实 RagFlow、WeKnora、MaxKB 适配。
- 知识库检索、问答和 MCP 服务。
- Web UI。
- 强身份认证或防本机恶意用户伪造身份。

## 3. 部署与安全边界

正式部署采用 CLI + 本机 HTTP 服务：

```text
普通用户
  -> wiki CLI
  -> 127.0.0.1 HTTP API
  -> wiki-manager-server
  -> SQLite 账本 + 原始文档归档 + 队列
  -> mock backend adapter
```

root 负责安装和初始化。默认目录集中放在 `/root/wiki-manager`：

```text
/root/wiki-manager/
  config/
    server.toml
  data/
    wiki.db
    archive/
    backend/
      mock/
  logs/
    server.log
  run/
    server.pid
```

服务只监听 `127.0.0.1`，不暴露内网端口。普通用户没有共享数据目录权限，所有操作通过 CLI 调用本机 API。CLI 每次请求读取当前 Linux 用户名，并通过请求头传给服务：

```text
X-Wiki-User: alice
```

服务端按该用户名在知识库成员表中的权限过滤可见范围。第一阶段采用内部可信 VM 模型：该设计避免普通用户直接访问共享归档和账本，但不防本机用户手写 HTTP 请求伪造 `X-Wiki-User`。

后端同步所需凭据由服务托管，用户通过 CLI 注册或更新凭据。第一阶段 mock 后端不需要真实凭据，但数据模型和配置位置为后续真实后端预留。

## 4. 核心实体

### User

不要求显式注册或登录。服务以 Linux 用户名标识用户，例如 `alice`、`bob`。用户第一次出现在成员授权、文档 owner 或操作日志中时即可被引用。

### KnowledgeBase

本工具维护的逻辑知识库，不依赖后端真实知识库。

主要字段：

- `id`
- `slug`
- `name`
- `description`
- `created_by`
- `status`
- `created_at`
- `updated_at`

第一阶段由管理员创建。

### KnowledgeBaseMember

知识库成员权限表。

主要字段：

- `kb_id`
- `linux_user`
- `role`
- `created_at`
- `updated_at`

`role` 取值：

- `viewer`：只读文档元数据、版本号和同步状态。
- `contributor`：可添加文档，并可更新、删除自己拥有的文档。
- `admin`：可管理该知识库成员、文档和同步目标。

第一阶段全局管理员由 `/root/wiki-manager/config/server.toml` 中的 `admins` 列表定义，默认包含 `root`。服务启动和每次管理员接口调用都读取该配置；后续阶段再考虑把全局管理员迁移到账本表中。

### Document

逻辑文档。默认由文件名生成 `slug`，冲突时自动追加后缀；后续可支持重命名或显示名调整。

主要字段：

- `id`
- `slug`
- `title`
- `owner_user`
- `current_version_id`
- `status`
- `created_at`
- `updated_at`
- `deleted_at`

一个文档可关联多个知识库。

### DocumentVersion

不可变版本。每次添加或更新文件都创建新版本，原始文件复制到归档目录。

主要字段：

- `id`
- `doc_id`
- `version_no`
- `original_filename`
- `content_hash`
- `file_size`
- `mime_type`
- `archive_path`
- `created_by`
- `created_at`

更新后逻辑文档指向最新版本，历史版本保留。

### DocumentKnowledgeBase

文档与知识库的多对多归属关系。

主要字段：

- `doc_id`
- `kb_id`
- `added_by`
- `status`
- `created_at`
- `deleted_at`

### BackendTarget

知识库同步目标。第一阶段数据模型支持多个目标，但只开放默认 `mock`。

主要字段：

- `id`
- `kb_id`
- `slug`
- `backend_type`
- `config_json`
- `status`
- `created_at`
- `updated_at`

### SyncJob 和 SyncState

`SyncJob` 记录待执行任务，例如创建、更新、删除和重试。`SyncState` 记录“文档 × 知识库 × backend target”的当前后端状态。

任务状态：

- `pending`
- `running`
- `succeeded`
- `failed`
- `cancelled`

同步状态：

- `not_synced`
- `synced`
- `sync_failed`
- `delete_pending`
- `deleted`
- `delete_failed`

后端若不支持原生更新，适配器使用“删除旧后端文档 + 上传新版本”的方式完成更新。第一阶段 mock 后端按该语义模拟。

## 5. 权限与可见性

第一阶段使用混合可见性模型：

- 文档元数据、版本号、同步状态：按 KB 权限可见。
- 原始文件下载：默认仅 owner、KB admin、全局 admin 可访问。
- 更新和软删除：默认仅 owner、KB admin、全局 admin 可操作。
- 硬清理 `purge`：默认仅 owner、KB admin、全局 admin 可操作，并要求显式确认。
- viewer 只读。
- contributor 可添加文档和管理自己的文档。
- KB admin 可管理该 KB 的成员、文档和同步目标。
- 全局管理员可创建 KB、管理全局队列并查看所有文档。

普通用户访问不存在或无权访问的 KB/文档时，服务优先返回 `404`，避免泄露资源名称。

## 6. 命令形态

命令保持短、语义明确，避免把内部队列概念暴露得过重。

管理员初始化：

```bash
wiki server init
wiki server start
wiki kb create frontend-docs --name "前端组知识库"
wiki kb grant frontend-docs alice contributor
wiki kb grant frontend-docs bob viewer
```

普通用户添加文档：

```bash
wiki add ./接口说明.pdf --kb frontend-docs
wiki add ./需求说明.docx --kb frontend-docs --later
wiki add ./通用规范.pdf --kb frontend-docs --kb backend-docs --later
```

语义：

- 不带 `--later`：创建文档、保存原始文件、创建立即同步任务，并尝试同步到 mock 后端。
- 带 `--later`：创建文档、保存原始文件、创建 pending 任务，不立即同步。
- 多个 `--kb`：同一个逻辑文档归属多个 KB，每个 KB 有独立同步状态。

更新文档：

```bash
wiki update 接口说明 ./接口说明-v2.pdf
wiki update 接口说明 ./接口说明-v2.pdf --later
```

删除和清理：

```bash
wiki delete 接口说明
wiki purge 接口说明
```

查看状态：

```bash
wiki kb list
wiki docs --kb frontend-docs
wiki doc 接口说明
wiki status
wiki sync
```

语义：

- `kb list`：普通用户只看到自己有权限的 KB。
- `docs --kb`：展示该 KB 下文档元数据、owner、当前版本、同步状态。
- `doc`：展示单个文档详情、版本历史、归属 KB、任务状态。
- `status`：查看当前用户相关的待同步或失败任务；管理员可看全局。
- `sync`：消费当前可执行的 pending/failed 任务。夜间统一入库可由 cron 调 `wiki sync`，管理员可调 `wiki sync --all`。

服务命令：

```bash
wiki server start
wiki server stop
wiki server status
```

第一阶段 `wiki server start` 启动本机后台服务，并将 pid 写入 `/root/wiki-manager/run/server.pid`；`wiki server stop` 根据 pid 停止服务；`wiki server status` 检查 pid 和 `/health`。systemd 单元文件作为后续增强或部署文档示例，不进入第一阶段必做范围。

## 7. HTTP API

服务 API 只监听 `127.0.0.1`。CLI 每次请求带 `X-Wiki-User`。

核心 API：

```text
GET  /health

POST /admin/init
POST /kbs
GET  /kbs
POST /kbs/{kb_slug}/members
GET  /kbs/{kb_slug}/members

POST /docs
GET  /docs?kb=frontend-docs
GET  /docs/{doc_slug}
POST /docs/{doc_slug}/versions
POST /docs/{doc_slug}/delete
POST /docs/{doc_slug}/purge

GET  /status
POST /sync
```

`POST /docs` 接收 multipart 文件和参数：

```text
file=<binary>
kb[]=frontend-docs
kb[]=backend-docs
later=true|false
```

服务端内部按分层组织：

```text
Typer CLI
  -> HttpClient
FastAPI/HTTP service
  -> Application services
  -> Repositories
  -> SQLite + archive storage
  -> Backend adapters
```

推荐模块边界：

- `domain`：实体、权限枚举、状态机。
- `application`：添加文档、更新文档、删除文档、同步任务等用例。
- `storage`：SQLite repository、文件归档。
- `adapters`：mock backend，未来扩展 RagFlow/MaxKB/WeKnora。
- `server`：HTTP API。
- `cli`：命令行客户端。

## 8. 错误处理

- 权限不足：返回 `403`，CLI 显示“无权限访问该知识库/文档”。
- KB 不存在或用户不可见：普通用户返回 `404`；管理员可得到更明确错误。
- 文件类型不支持：返回 `400`。第一阶段允许 `pdf/doc/docx/xls/xlsx/ppt/pptx/txt/md`，并允许通过配置调整。
- 同步失败：任务进入 `failed`，保留错误摘要，可通过 `wiki status` 查看并用 `wiki sync` 重试。
- 删除同步失败：本地仍标记 deleted，但后端同步状态为 `delete_failed`，后续重试。
- 服务不可达：CLI 显示本机服务未启动，并提示检查 `wiki server status`。

CLI 输出应默认适合人类阅读，后续可增加 `--json` 供自动化使用。错误和进度输出走 stderr，结构化结果走 stdout。

## 9. 测试策略

单元测试：

- slug 生成和冲突处理。
- 权限判断。
- 版本号递增。
- 文档状态和同步状态机。

存储测试：

- SQLite repository。
- archive 文件复制。
- content hash 记录。
- 软删除和 purge。

应用服务测试：

- add/update/delete/purge/sync 完整用例。
- 多 KB 归属。
- 立即同步和计划同步。
- mock 后端更新语义。

API 测试：

- viewer/contributor/admin 权限。
- `X-Wiki-User` 过滤。
- 普通用户不可见资源返回 `404`。

CLI 测试：

- 命令参数解析。
- HTTP client 请求构造。
- 常见错误提示。

端到端烟测：

1. 初始化服务。
2. 创建 KB。
3. 授权用户。
4. 添加文档。
5. 创建计划同步。
6. 执行 `wiki sync`。
7. 更新文档。
8. 软删除文档。
9. 查看状态和版本历史。

## 10. 后续阶段

第二阶段候选方向：

- 接入真实后端：优先从 WeKnora、RagFlow、MaxKB 中选择一个。
- 增加基础查询：知识库列表、文档检索、问答。
- 增加只读 MCP 服务。
- 增加后端迁移和重放能力。

第三阶段候选方向：

- API 文档采集和 Markdown 生成。
- 多来源同步。
- 多后端混合检索。
- 更强身份认证：Unix socket peer credentials、token 或正式登录。
- Web 管理界面。
