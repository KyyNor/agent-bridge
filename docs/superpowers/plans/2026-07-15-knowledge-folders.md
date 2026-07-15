# Knowledge Document Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Agent Bridge 中为文档知识库增加知识库级目录树，使本地文档按目录管理和展示；WeKnora 同步时保留相对目录路径，RAGFlow、PageIndex、Mock 同步时继续平铺；同一全局文档在不同知识库中可以拥有不同目录位置，删除目录只影响当前知识库关联。

**Architecture:** `documents` 继续保存全局文档实体，`document_kbs.folder_id` 保存文档在某个知识库中的目录位置。每个知识库拥有一个不参与同步路径的虚拟根目录。目录树和目录化上传由服务层统一处理，后端适配器通过能力声明决定是否接收目录路径。WeKnora 使用其公开上传接口的 `fileName` 字段保存相对路径；没有目录能力的后端忽略该路径并使用文件 basename。

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite, pytest, httpx, Vue 3, TypeScript, npm。

---

## 已知基线与验收口径

当前工作树的完整测试命令 `uv run pytest -q` 会在收集阶段因已有问题失败：`tests/test_capability_delete.py` 导入 `tests.test_capability_service` 时出现 `ModuleNotFoundError: No module named 'tests'`。该问题发生在本功能改动之前，按需求不修复、不作为本计划的阻塞条件。每个实现任务使用不依赖该导入的聚焦测试；最后仍运行完整测试并把该已知收集错误单独记录为非阻塞结果。

计划中的每个实现任务均先写失败测试，再写最小实现，再运行该任务的聚焦测试。每个任务完成后单独提交一次，避免目录存储、业务语义、后端同步和前端展示互相掩盖问题。

## Task 1: 增加目录数据模型、迁移和存储层

**Files:**

- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/storage/repositories/knowledge.py`
- Add: `src/agent_bridge/storage/repositories/folders.py`
- Add: `tests/test_knowledge_folders.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: 写目录存储失败测试。** 在 `tests/test_knowledge_folders.py` 覆盖以下行为：
  - 新建知识库后存在唯一根目录；根目录 `is_root=1`、`parent_id=NULL`、展示路径为空。
  - 已有数据库迁移后，每个旧知识库自动补根目录；所有旧的活动 `document_kbs` 记录回填到对应根目录；迁移重复执行不会创建第二个根目录。
  - 同一父目录下不能创建重名目录；目录名拒绝空字符串、`/`、`\\`、`.`、`..`、控制字符和路径穿越片段。
  - 目录不能移动到自身或自身后代；根目录不能重命名、移动或删除。
  - 同一文档可以在 KB A 的 `A/组件` 和 KB B 的 `公共资料` 下分别存在，查询关联时返回各自的 `folder_id` 和 `folder_path`。
  - 目录统计能返回当前知识库子树中的目录数、不同文档数和直接文档数。

- [ ] **Step 2: 定义 SQLite 表和索引。** 在 `schema.py` 的新库 schema 中加入：
  - `knowledge_folders(id INTEGER PRIMARY KEY AUTOINCREMENT, kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE, parent_id INTEGER REFERENCES knowledge_folders(id) ON DELETE CASCADE, name TEXT NOT NULL, is_root INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`。
  - `document_kbs.folder_id INTEGER REFERENCES knowledge_folders(id)`，保留旧主键 `(doc_id, kb_id)`，使字段属于关联而不是全局文档。
  - `backend_folder_mappings(id INTEGER PRIMARY KEY AUTOINCREMENT, kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE, backend_slug TEXT NOT NULL, folder_id INTEGER NOT NULL REFERENCES knowledge_folders(id) ON DELETE CASCADE, backend_folder_id TEXT NOT NULL, path_snapshot TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(kb_id, backend_slug, folder_id))`。
  - 为 `(kb_id, parent_id, name)`、`document_kbs(kb_id, folder_id, status)` 和映射表查询列建立索引；使用 SQLite partial unique index 保证每个 KB 只能有一个 `parent_id IS NULL AND is_root=1` 的根目录。

- [ ] **Step 3: 实现幂等迁移。** 在 `sqlite.py` 的现有迁移入口中：
  - 通过 `PRAGMA table_info(document_kbs)` 判断后再执行 `ALTER TABLE document_kbs ADD COLUMN folder_id INTEGER`，避免重复迁移失败。
  - 为所有 active KB 调用 `ensure_root_folder`；对 `folder_id IS NULL` 的旧关联回填对应根目录。
  - 对新表、索引和旧库数据迁移全部使用同一个事务；迁移完成后新增关联必须不再允许空 `folder_id`。
  - 保留旧数据库中已有文档的 slug、版本号、内容 hash、归档路径和同步状态不变。

- [ ] **Step 4: 实现 `FolderRepository`。** 在 `folders.py` 提供明确的方法：
  - `ensure_root_folder(kb_id)`、`get_root_folder(kb_id)`、`get_folder(kb_id, folder_id)`。
  - `create_folder(kb_id, parent_id, name)`、`rename_folder(kb_id, folder_id, name)`、`move_folder(kb_id, folder_id, parent_id)`。
  - `list_folder_tree(kb_id)`，返回 `id`、`parent_id`、`name`、`is_root`、`path`、`direct_file_count`、`descendant_file_count`、`descendant_folder_count`。
  - `get_subtree_ids(kb_id, folder_id)`、`get_subtree_counts(kb_id, folder_id)`、`delete_folder_subtree(kb_id, folder_id)`；删除方法只删除目录行和当前 KB 的关联，不直接删除 `documents`。
  - `upsert_backend_folder_mapping`、`get_backend_folder_mapping`、`delete_backend_folder_mappings`，其中 WeKnora 的 `backend_folder_id` 暂存规范化远端路径。
  - 所有方法在 SQL 中同时校验 `kb_id`，不能通过其他 KB 的 `folder_id` 读写目录。

- [ ] **Step 5: 扩展知识 repository 和 SQLite facade。**
  - 修改 `KnowledgeRepository.attach_document_to_kb`，要求传入 `folder_id`，冲突恢复 active 关联时同时更新目录。
  - 修改 `list_docs_for_kb(kb_id, folder_id=None)`；`folder_id=None` 保持旧行为返回整个 KB，传入目录时只返回该目录的直接文件。
  - 返回文档关联的 `folder_id`、`folder_path`，并提供 `get_document_placement(doc_id, kb_id)`、`update_document_placement(doc_id, kb_id, folder_id)`。
  - 新增只解除单个 KB 关联的方法，禁止目录删除流程调用现有全局 `soft_delete_document`。
  - 在 `sqlite.py` 暴露上述目录和 placement 方法，保持 service 不直接操作连接对象。

- [ ] **Step 6: 运行聚焦测试并提交。**

  ```bash
  uv run pytest -q tests/test_knowledge_folders.py tests/test_storage.py
  ```

  预期：新增目录存储测试和既有存储测试通过；不触发 `tests.test_capability_service` 的已知收集错误。

  ```bash
  git diff --check
  git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/knowledge.py src/agent_bridge/storage/repositories/folders.py tests/test_knowledge_folders.py tests/test_storage.py
  git commit -m "feat: add knowledge folder storage"
  ```

## Task 2: 实现目录服务、文档 placement API 和递归删除

**Files:**

- Modify: `src/agent_bridge/api/schemas.py`
- Modify: `src/agent_bridge/api/routes/knowledge.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `tests/test_services.py`
- Add: `tests/test_knowledge_folder_api.py`

- [ ] **Step 1: 写服务和 API 失败测试。** 覆盖：
  - 管理员可以创建、重命名、移动和查询目录；跨 KB 的 `folder_id`、循环移动和根目录变更返回 400/404。
  - `GET /docs?kb=...&folder_id=...` 只返回所选目录的直接文件；不传 `folder_id` 仍返回该 KB 全部文件，保留旧客户端兼容性。
  - 上传请求传入单个 KB 的 `folder_id` 后，文档关联落在该目录；不传目录时落在根目录。
  - `POST /kbs/{kb_slug}/docs/{doc_slug}/delete` 只解除当前 KB 关联。文档同时属于其他 KB 时，其他 KB 目录和文档仍可查询。
  - 删除目录第一次未确认时返回 409 和实时重算的 `directory_count`、`file_count`；确认后递归删除目录及当前 KB 关联。
  - 共享文档从最后一个 KB 删除前不删除全局文档；最后一个关联删除后才进入全局 deleted 状态和远端删除流程。

- [ ] **Step 2: 增加 Pydantic 请求模型。** 在 `schemas.py` 增加并约束：
  - `CreateFolderRequest(parent_folder_id: int | None, name: str)`。
  - `UpdateFolderRequest(name: str | None, parent_folder_id: int | None)`，至少提供一个字段。
  - `DeleteFolderRequest(confirm: bool = False)`。
  - `DocumentPlacementRequest(kb: str, folder_id: int)`。
  - `AttachDocumentRequest(kb: str, folder_id: int)`。
  - 上传表单新增 `folder_id: int | None` 和 `relative_path: str | None`；`folder_id` 只允许在单 KB 请求中使用，多个 KB 请求仍可不带目录并全部落根目录。

- [ ] **Step 3: 实现服务层目录操作和校验。** 在 `service.py`：
  - `list_folders(actor, kb_slug)`、`create_folder(actor, kb_slug, parent_folder_id, name)`、`update_folder(...)`、`delete_folder(...)` 全部先复用 KB 可见性和管理员校验。
  - 目录名称统一调用路径规范化函数；查询父目录、目标目录和同级重名均限定在同一个 KB。
  - `delete_folder` 在 `confirm=False` 时只返回实时统计，不修改数据；`confirm=True` 时在一个事务中取子树、解除当前 KB 文档关联并删除目录。
  - 对每个被解除的关联按当前 KB、每个 active backend target 取消未执行 create/update 任务；若该目标已有远端文档状态则生成当前 KB 的 delete 任务。
  - 解除关联后若文档不再有任何 active KB 关联，才调用全局文档软删除；归档文件仍遵循已有 purge 生命周期。
  - `remove_document_from_kb` 成为详情页删除的唯一服务入口；保留旧全局 `delete_document` 作为兼容入口，但新前端不再调用它。

- [ ] **Step 4: 实现 placement、attach 和移动任务生成。**
  - `place_document(actor, doc_slug, kb_slug, folder_id)` 校验关联存在、目录属于 KB、目标不是当前目录后更新 `document_kbs.folder_id`。
  - 修改目录后取消该文档该 KB/backend 的未执行 create/update/move 任务；若远端已有文档且 backend 支持目录，生成一个 move 任务；若远端尚未创建，保留一个 create 任务并让执行时读取最新 placement。
  - `attach_document` 允许把已有全局文档关联到另一个 KB 的指定目录，默认不复制 `documents` 行；为新关联生成该 KB 的 create 任务。

- [ ] **Step 5: 暴露 API 路由。** 在 `routes/knowledge.py` 添加：
  - `GET /kbs/{kb_slug}/folders`
  - `POST /kbs/{kb_slug}/folders`
  - `PATCH /kbs/{kb_slug}/folders/{folder_id}`
  - `DELETE /kbs/{kb_slug}/folders/{folder_id}`，body 为 `DeleteFolderRequest`；未确认返回统计，确认后返回删除统计。
  - 给现有 `GET /docs` 增加 `folder_id` query 参数。
  - 给现有 `POST /docs` 增加 `folder_id`、`relative_path` form 参数。
  - `POST /kbs/{kb_slug}/docs/{doc_slug}/delete`
  - `PATCH /docs/{doc_slug}/placement`
  - `POST /docs/{doc_slug}/attach`
  - 文档列表和详情响应增加 placement 字段，但不改变已有字段名和旧查询语义。

- [ ] **Step 6: 运行服务/API 测试并提交。**

  ```bash
  uv run pytest -q tests/test_knowledge_folder_api.py tests/test_knowledge_folders.py tests/test_services.py
  ```

  预期：目录 CRUD、placement、跨 KB 共享文档删除和递归删除测试通过；若 `tests/test_services.py` 触发与目录无关的既有导入问题，只记录该问题并继续执行目录 API 的独立测试。

  ```bash
  git diff --check
  git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/knowledge.py src/agent_bridge/app/service.py tests/test_services.py tests/test_knowledge_folder_api.py
  git commit -m "feat: add folder management and scoped document deletion"
  ```

## Task 3: 保留单文件、浏览器文件夹、ZIP 和 Git 的相对路径

**Files:**

- Modify: `src/agent_bridge/knowledge_management/docs_knowledge/uploads.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `src/agent_bridge/api/routes/knowledge.py`
- Add: `src/agent_bridge/app/document_paths.py`
- Modify: `tests/test_services.py`
- Add: `tests/test_uploads.py`
- Modify: `tests/test_knowledge_folder_api.py`

- [ ] **Step 1: 写路径保留失败测试。** 覆盖：
  - 普通文件传入 `guide.md` 时使用当前目录；传入 `docs/guide.md` 时创建 `docs` 子目录并把 `document_versions.original_filename` 保存为相对路径。
  - ZIP 中 `A/guide.md`、`A/B/spec.md` 解压后仍返回各自的相对路径；绝对路径、`..`、符号链接和嵌套压缩包继续被拒绝。
  - Git 仓库中的 `docs/api/guide.md` 以仓库根为基准创建 `docs/api`，根目录文件不产生名为根目录的额外层级。
  - Windows `\\` 分隔符规范化成 `/`；路径不能逃出当前 KB 目录。

- [ ] **Step 2: 增加统一相对路径模型。** 在 `document_paths.py` 提供：
  - `normalize_relative_document_path(raw_path) -> str`，将分隔符统一为 `/`，去除重复分隔符，拒绝空 basename、绝对路径、`.`、`..`、控制字符和超过长度限制的单段名称。
  - `split_document_path(path) -> tuple[list[str], str]`，返回父目录段和 basename。
  - `join_backend_path(folder_path, relative_path) -> str`，根目录为空时不生成 `root/` 前缀。
  - 该模块只处理逻辑路径，不直接访问文件系统；ZIP 安全校验仍由 `uploads.py` 负责。

- [ ] **Step 3: 修改 ZIP 提取结果。** 将 `extract_zip_documents` 的返回值改为带 `path` 和 `relative_path` 的不可变记录，而不是只返回临时文件 `Path`。调用方必须使用记录中的相对路径，现有临时目录清理逻辑保持不变。更新 ZIP 测试验证嵌套目录和清理行为。

- [ ] **Step 4: 修改服务入库参数和目录解析。**
  - `add_document`、`_add_single_document`、`_add_zip_documents` 增加 `relative_path` 参数；普通文件默认使用 `original_filename` 或 `source.name`。
  - 对每个目标 KB，以选定 `folder_id` 为基准，根据相对路径父目录调用 `ensure_folder_path`；无显式目录时从该 KB 根目录开始。
  - 多 KB 上传允许使用相同的相对路径并在各 KB 根目录下创建同名子目录；显式 `folder_id` 与多个 KB 同时出现时返回 400，因为一个目录 ID 不能属于多个 KB。
  - 文档标题取 basename 的 stem，版本原始文件名保存规范化相对路径；slug 仍基于 basename 生成并保持全局唯一。
  - 重复内容在目标 KB 已存在时不新建文档；如果已有 placement，不因一次重复上传隐式移动，响应继续标记 `skipped`。

- [ ] **Step 5: 修改 Git 同步路径。** 在 `sync_kb_repo_source_changes` 中计算 `path.relative_to(local_path).as_posix()`，将其传入 `_import_repo_file` 和 `add_document`。仓库根目录文件直接使用 basename，`A/B/file.md` 使用 `A/B/file.md`，不生成 `root/A/B`。

- [ ] **Step 6: 运行路径测试并提交。**

  ```bash
  uv run pytest -q tests/test_uploads.py tests/test_knowledge_folder_api.py tests/test_services.py -k 'zip or folder or path or repo or git'
  ```

  预期：相对路径保留、ZIP 安全校验、Git 根路径语义测试通过；与本功能无关的服务测试失败不改变该任务验收结论。

  ```bash
  git diff --check
  git add src/agent_bridge/knowledge_management/docs_knowledge/uploads.py src/agent_bridge/app/service.py src/agent_bridge/api/routes/knowledge.py src/agent_bridge/app/document_paths.py tests/test_services.py tests/test_uploads.py tests/test_knowledge_folder_api.py
  git commit -m "feat: preserve document relative paths"
  ```

## Task 4: 增加后端能力声明并实现 WeKnora 路径同步

**Files:**

- Modify: `src/agent_bridge/core/domain.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `src/agent_bridge/storage/repositories/knowledge.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/knowledge_management/docs_knowledge/backends/weknora.py`
- Modify: `src/agent_bridge/knowledge_management/docs_knowledge/backends/ragflow.py`
- Modify: `src/agent_bridge/knowledge_management/docs_knowledge/backends/pageindex.py`
- Modify: `src/agent_bridge/knowledge_management/docs_knowledge/backends/mock.py`
- Modify: `tests/test_weknora_backend.py`
- Modify: `tests/test_ragflow_backend.py`
- Modify: `tests/test_pageindex_backend.py`
- Add: `tests/test_folder_sync.py`

- [ ] **Step 1: 写适配器和同步失败测试。** 覆盖：
  - WeKnora 上传 `A/B/guide.md` 时，HTTP 请求仍是 `POST /api/v1/knowledge-bases/{id}/knowledge/file`，multipart 的 `fileName` 和文件名均为 `A/B/guide.md`。
  - WeKnora 根目录文件的 `fileName` 只有 `guide.md`，不出现 `root/guide.md`。
  - RAGFlow、PageIndex、Mock 的能力声明为 `supports_folders=False`，上传时只使用 basename，不调用任何目录或移动接口。
  - WeKnora 现有文档移动会先删除旧远端文档，再按新相对路径重传当前归档；新上传失败时任务为 failed，错误被保留，不能静默降级为根目录上传。
  - 目录移动后，同一文档在 WeKnora 目标上生成 move 任务；平铺后端不生成 move 任务。
  - 同一个文档分别绑定多个 backend target 时，各 target 使用自己的能力和同步路径。

- [ ] **Step 2: 扩展领域契约。** 在 `domain.py`：
  - 给 `Operation` 增加 `move`。
  - 增加 `BackendCapabilities(supports_folders: bool)`。
  - 给 `BackendAdapter` 增加 `capabilities()`；将 `upload` 扩展为接收可选 `remote_path`；增加 `move(backend_kb_id, backend_doc_id, file_path, filename, remote_path) -> str`，返回替换后的远端文档 ID。
  - 保持已有 upload 调用的参数顺序和默认行为，避免旧适配器调用方立即失效。

- [ ] **Step 3: 实现四个适配器的能力。**
  - `WeknoraBackend.capabilities()` 返回 `supports_folders=True`。
  - WeKnora `upload` 把 `remote_path or filename` 同时作为 multipart 文件名和 `fileName` form 字段，调用官方文档中的 `/api/v1/knowledge-bases/{id}/knowledge/file`。路径先通过本地规范化函数校验。
  - WeKnora `move` 调用已有 delete，再调用 upload；由于当前公开 REST 文档提供的是 `fileName` 路径上传而非独立文件夹移动接口，计划不伪造目录 API。远端目录层级由 WeKnora 根据 `fileName` 路径展示。
  - `RagFlowBackend`、`PageIndexBackend`、`MockBackend` 返回 `supports_folders=False`；它们在 upload 中忽略 `remote_path`，继续提交 basename。
  - 更新测试 fake adapter 和所有直接实现 `BackendAdapter` 的测试替身以实现新方法。

- [ ] **Step 4: 实现同步路径解析和映射。**
  - 从文档当前 KB placement 计算不含虚拟根的本地路径。
  - 对 `supports_folders=True` 的 target，在 `backend_folder_mappings` 中以 `(kb_id, backend_slug, folder_id)` upsert；WeKnora 当前 `backend_folder_id` 保存规范化路径，`path_snapshot` 保存同一值，便于后续真实远端 folder ID 接入。
  - 对不支持目录的 target 不写 mapping，不调用 `ensure_folder_path` 或 move。
  - `_run_job` 的 create/update 在执行时重新读取 placement，避免任务排队期间使用旧目录。
  - move job 读取当前归档版本和目标路径，调用 adapter.move；成功后更新 `sync_states.backend_doc_id`，失败则更新 job 和 sync state 的错误字段。
  - 目录删除后清理无关联的 mapping；不删除仍被其他文档使用的目录路径记录。

- [ ] **Step 5: 调整同步任务生命周期。**
  - `create_sync_job`、查询和排序支持 `Operation.move`。
  - 文档 placement 发生变化时取消同一文档/KB/backend 尚未执行的旧 create/update/move 任务；远端不存在时只保留 create，远端存在且 backend 支持目录时创建 move。
  - 删除目录或 KB 关联时继续只为当前 KB/backend 创建 delete；不因共享文档在另一个 KB 仍存在而删除另一 KB 的远端文档。
  - 所有适配器异常都进入现有失败状态和重试路径，WeKnora 路径处理异常不得 fallback 到平铺。

- [ ] **Step 6: 运行后端同步测试并提交。**

  ```bash
  uv run pytest -q tests/test_folder_sync.py tests/test_weknora_backend.py tests/test_ragflow_backend.py tests/test_pageindex_backend.py
  ```

  预期：WeKnora 路径上传/移动、三种平铺后端兼容性和失败重试测试通过。

  ```bash
  git diff --check
  git add src/agent_bridge/core/domain.py src/agent_bridge/app/service.py src/agent_bridge/storage/repositories/knowledge.py src/agent_bridge/storage/sqlite.py src/agent_bridge/knowledge_management/docs_knowledge/backends/weknora.py src/agent_bridge/knowledge_management/docs_knowledge/backends/ragflow.py src/agent_bridge/knowledge_management/docs_knowledge/backends/pageindex.py src/agent_bridge/knowledge_management/docs_knowledge/backends/mock.py tests/test_weknora_backend.py tests/test_ragflow_backend.py tests/test_pageindex_backend.py tests/test_folder_sync.py
  git commit -m "feat: sync folder paths to weknora"
  ```

## Task 5: 实现详情页目录树，并移除列表页上传入口

**Files:**

- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/views/knowledge/KnowledgeView.vue`
- Add: `frontend/capabilities/src/components/knowledge/FolderTree.vue`
- Add: `frontend/capabilities/src/components/knowledge/FolderDialogs.vue`

- [ ] **Step 1: 先完成 API 类型和客户端方法。**
  - 在 `types.ts` 增加 `KnowledgeFolder`、`FolderCounts`、`DocumentPlacement` 类型；`Document` 增加 `folder_id`、`folder_path`。
  - 在 `client.ts` 增加 `listFolders(kb)`、`createFolder(kb, parentFolderId, name)`、`updateFolder(kb, folderId, payload)`、`deleteFolder(kb, folderId, confirm)`。
  - 修改 `listDocs(kb, backend?, folderId?)`，仅在参数存在时发送 `folder_id`。
  - 修改 `addDocument(file, kbs, later, folderId?, relativePath?)`，以 `folder_id`、`relative_path` form 字段提交。
  - 增加 `deleteDocumentFromKb(kb, slug)`、`placeDocument(slug, kb, folderId)`、`attachDocument(slug, kb, folderId)`。
  - 保留旧 `deleteDocument(slug)` 以兼容其他页面，但知识库详情页全部改用 KB-scoped 方法。

- [ ] **Step 2: 建立目录树组件。** `FolderTree.vue` 接收树数据和当前 folder ID，展示：
  - 根节点使用知识库名称，作为本地虚拟根；不把 `root` 作为路径段。
  - “全部文档”作为快捷筛选项，不作为 folder ID 或同步路径。
  - 每个目录的直接/后代文件数和目录数；选中目录时 emit `select`。
  - 目录新建、重命名、移动、删除事件由父视图触发，组件不直接持有 API 状态。

- [ ] **Step 3: 重构详情页布局和数据加载。** 在 `KnowledgeView.vue`：
  - 知识库详情采用左侧 `FolderTree`、右侧面包屑和当前目录直接文档列表。
  - 进入详情页默认选择根目录并调用 `listDocs(kb, backend, rootFolderId)`；“全部文档”才调用不带 `folder_id` 的兼容查询。
  - 切换目录只刷新文档列表，不切换 KB；面包屑显示当前目录完整路径。
  - 目录变更后刷新树和当前列表，保存当前选中的有效目录；被删除目录自动回到其父目录。
  - backend 状态说明：WeKnora 显示“目录同步”，RAGFlow/PageIndex/Mock 显示“本地分目录，后端平铺”。

- [ ] **Step 4: 接入目录 CRUD 和递归删除确认。**
  - 新建和重命名使用表单校验，展示后端返回的名称错误。
  - 移动目录提供目标目录树，禁止选择自身及后代；成功后刷新树。
  - 删除目录先调用 `confirm=false` 取得实时计数，再显示精确提示：`你删除的目录下有 N 个目录、M 个文件，确认删除后都会删除且不能恢复。`；用户确认后再次调用 `confirm=true`。
  - 删除接口返回计数变化时，前端以最终接口结果为准，不使用第一次预览的过期数字。
  - 根目录隐藏删除、重命名和移动操作。

- [ ] **Step 5: 修改上传入口和相对路径采集。**
  - 删除知识库列表行约现有 `openUploadDialog(k)` 的“上传”按钮及其无目录上下文调用。
  - 保留详情页上传按钮，默认使用当前选中目录；进入详情时因此仍可向根目录上传。
  - 用 `{ file: File, relativePath: string }` 保存上传项；普通文件使用 basename，文件夹选择使用 `webkitRelativePath`，拖拽递归时累积目录前缀。
  - ZIP 文件的 `relativePath` 从 ZIP 成员名读取；上传时以当前选中目录为基准，不额外添加 ZIP 文件名目录。
  - 上传成功后刷新目录树和当前目录列表；重复文件显示现有 skipped 原因。

- [ ] **Step 6: 修改详情页文档删除、移动和批量操作。**
  - `deleteDoc`、`batchDeleteDocs` 改为调用当前 KB 的删除 endpoint，不能再触发全局文档删除。
  - 文档移动使用目录选择器调用 `placeDocument`；关联到其他 KB 使用 `attachDocument`，目标目录必须来自目标 KB 的树。
  - 列表展示 `folder_path`、同步状态和后端能力说明，搜索仍按整个 KB 搜索，不增加目录权限或目录检索隔离。

- [ ] **Step 7: 运行前端检查并提交。**

  ```bash
  cd frontend/capabilities
  npm run typecheck
  npm run build
  ```

  预期：TypeScript 类型检查和生产构建通过；构建产物不提交到 Git。

  ```bash
  cd /Users/kyynor/Code/agent-bridge/.worktrees/knowledge-folders-plan
  git diff --check
  git add frontend/capabilities/src/api/client.ts frontend/capabilities/src/api/types.ts frontend/capabilities/src/views/knowledge/KnowledgeView.vue frontend/capabilities/src/components/knowledge/FolderTree.vue frontend/capabilities/src/components/knowledge/FolderDialogs.vue
  git commit -m "feat: add knowledge folder tree UI"
  ```

## Task 6: 集成回归、迁移兼容和最终验收

**Files:**

- Modify: `tests/test_e2e.py`
- Modify: `tests/test_services.py`
- Modify: `tests/test_storage.py`
- Modify: `docs/superpowers/specs/2026-07-15-knowledge-folders-design.html`

- [ ] **Step 1: 增加端到端场景。** 使用现有测试 fixture 验证完整链路：
  - 旧数据库初始化后旧文档位于根目录，旧 `GET /docs?kb=...` 结果不变。
  - 浏览器/ZIP/Git 导入 `A/B/file.md` 后，本地树是 `A -> B -> file.md`，同步到 WeKnora 的 `fileName` 是 `A/B/file.md`。
  - 同一文档在两个 KB 的目录不同，移动或删除 KB A 的关联不影响 KB B。
  - RAGFlow、PageIndex、Mock 本地目录查询正常，远端上传文件名仍是 basename。
  - 目录递归删除返回准确目录数和文件数，确认后不可恢复当前 KB 关联；根目录拒绝删除。
  - 现有列表页不再出现上传按钮，详情页仍能从根目录上传。

- [ ] **Step 2: 补充数据一致性检查。** 测试迁移和任务竞争场景：
  - 两次迁移和两次创建根目录不会产生重复根。
  - 目录确认期间子树新增文档时，服务端以确认请求时的实时计数和事务快照执行。
  - 文档移动连续发生两次时，旧 move/create 任务被取消，最终同步路径来自最后一次 placement。
  - WeKnora 重传失败不会把同步状态伪装成成功，也不会自动改为根目录上传。

- [ ] **Step 3: 更新设计说明。** 在已提交的 HTML 设计说明中补充实现事实：WeKnora 采用 `/api/v1/knowledge-bases/{id}/knowledge/file` 的 `fileName` 相对路径字段；当前公开 API 没有独立远端目录 CRUD，因此移动通过删除旧远端知识后按新路径重传完成。保留 RAGFlow 官方文件管理器不满足知识库内目录语义、PageIndex Max/闭源付费能力不接入的结论。

- [ ] **Step 4: 运行最终聚焦验证。**

  ```bash
  uv run pytest -q tests/test_knowledge_folders.py tests/test_knowledge_folder_api.py tests/test_folder_sync.py tests/test_uploads.py tests/test_services.py tests/test_storage.py tests/test_weknora_backend.py tests/test_ragflow_backend.py tests/test_pageindex_backend.py
  ```

  预期：本功能相关测试通过；如果聚焦命令中仍被已知 `tests` 包导入问题阻断，则拆分为不导入该模块的测试文件运行，并在交付说明中保留原始错误。

  ```bash
  cd frontend/capabilities
  npm run typecheck
  npm run build
  cd /Users/kyynor/Code/agent-bridge/.worktrees/knowledge-folders-plan
  uv run pytest -q
  ```

  预期：前端两项通过；完整后端测试的已知 `ModuleNotFoundError: No module named 'tests'` 仍可出现，但不作为本功能失败条件。

- [ ] **Step 5: 检查差异并提交文档/回归测试。**

  ```bash
  git diff --check
  git status --short
  git add tests/test_e2e.py tests/test_services.py tests/test_storage.py docs/superpowers/specs/2026-07-15-knowledge-folders-design.html
  git commit -m "test: cover knowledge folder integration"
  ```

## 完成定义

- 本地每个知识库都有虚拟根目录，旧文档和未指定目录的上传均位于根目录。
- `document_kbs.folder_id` 是唯一的文档目录归属字段；同一全局文档在不同 KB 可以拥有不同目录。
- 目录树支持创建、重命名、移动、递归删除；根目录不能删除，删除确认文字包含实时的目录数和文件数。
- 列表页不再提供无目录上下文的上传；详情页从当前目录上传，支持单文件、浏览器文件夹、ZIP 和 Git 相对路径。
- WeKnora 保留相对路径；本地根目录不会生成远端 `root/` 前缀。RAGFlow、PageIndex、Mock 的远端行为保持平铺。
- 共享文档的 KB 级删除不会误删其他 KB 的目录、关联或远端文档。
- 聚焦后端测试和前端 typecheck/build 通过；完整测试的已知收集错误被单独记录而不是混入本功能回归结论。
