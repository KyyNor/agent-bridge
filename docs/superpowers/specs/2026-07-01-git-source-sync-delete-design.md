# Git 数据源删除与增量同步设计

**日期**: 2026-07-01
**主题**: 知识库 git 数据源的删除按钮 + 定时增量同步
**状态**: 待实现

## 背景与问题

当前 git 数据源(`kb_repo_sources`)存在两个核心缺陷:

1. **无法删除**:知识库的 git 数据源页面没有删除按钮。一旦关联,无法解绑,也无法清理它导入的文档。
2. **同步会产生重复**:`sync_kb_repo_source`(`src/agent_bridge/app/service.py:474`)每次同步都把仓库里所有匹配文件**全量重新添加**为新文档(`add_document`),没有变更检测。文件改名/修改/删除都不会增量反映;重复同步产生 `guide`、`guide-2`、`guide-3`… 这样的重复文档。
3. **无来源追踪**:`documents` 表没有任何字段记录文档来源,git 导入的文档和手动上传的文档无法区分。这是上述两个缺陷的根本数据障碍——不知道哪些文档属于哪个 git 仓库,就无法精准删除或 diff。

## 目标

1. 在知识库的 git 数据源页面提供**删除按钮**,二次确认后解绑关联,并软删除该 KB 下由该仓库提供的文档,生成 `Operation.delete` 同步任务。
2. 实现**定时增量同步**:在每轮文档后端同步(drain `sync_jobs`)之前,先做 git diff,对仓库文件变更生成 `Operation.create/delete` 同步任务。
3. diff 策略:移除的文件→直接删除;新增的文件→直接新增;修改的文件→**先删除再添加**(doc_id 变化)。

## 关键决策(已与用户确认)

| 决策点 | 选择 |
|--------|------|
| 删除范围(同一仓库可能被多 KB 引用) | 解绑该 KB 的关联 + 删该 KB 下文档;**保留** `code_repositories` 记录和本地克隆 |
| 触发机制 | 串行在 doc_sync drain **之前**:同一轮先 git diff 生成任务,再 drain sync_jobs |
| git 同步失败处理 | 跳过出错的源,记录 `last_error`,**不阻塞**后续 drain |
| 文档来源追踪粒度 | `documents` 表加 `source_type` + `source_repo_key` 字段(不新建映射表) |
| diff 匹配口径 | 按 `slug + repo_key` 匹配(不按文件路径) |
| 修改文件的同步方式 | 真·先删后加(软删旧文档 + 新建文档,doc_id 变化),不使用 `update_document` |
| 删除二次确认 | 预查并显示该源提供的文档数量 |
| 存量迁移 | 不回填,接受迁移后首次同步会重复(存量文档 `source_type` 默认 `'manual'`) |

## 已接受的权衡

- **修改 = doc_id 变化**:修改文件采用先删后加,旧 doc_id 消失、新 doc_id 产生。后端(如 pageindex)会收到一个 delete + 一个 create。这是用户明确要求的语义。
- **存量首次重复**:迁移后,历史 git 导入文档的 `source_type` 是默认值 `'manual'`,首次定时 diff 不认识它们,会当作新文件重新导入。用户需手动清理一次。后续同步即正常增量。

---

## 设计第 1 段:数据模型与来源追踪

### Schema 变更

`documents` 表(`src/agent_bridge/storage/schema.py:25-35`)新增两列:

| 列名 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `source_type` | `TEXT NOT NULL` | `'manual'` | `'manual'`(手动上传)/ `'git'`(git 数据源导入) |
| `source_repo_key` | `TEXT NOT NULL` | `''` | `source_type='git'` 时为来源 `repo_key`,否则为空 |

遵循代码库的**双写惯例**(参考 `knowledge_bases.default_backend_slug`):

1. **schema.py 的 `CREATE TABLE documents`** 加这两列(新库直接拥有)。
2. **`sqlite.py` 的 `migrate_phase2()`**(`src/agent_bridge/storage/sqlite.py:89`)新增一处 `_ensure_columns` 调用(老库升级):
   ```python
   self._ensure_columns(
       conn,
       "documents",
       {
           "source_type": "TEXT NOT NULL DEFAULT 'manual'",
           "source_repo_key": "TEXT NOT NULL DEFAULT ''",
       },
   )
   ```
   `_ensure_columns`(`sqlite.py:277-281`)通过 `PRAGMA table_info` 守卫,幂等安全。

`documents` 表此前**从未**被迁移过(无既有 `_ensure_columns` 调用),本次是首次为它建立迁移路径。

### 来源追踪的写入

- **`add_document`**(`app/service.py:261`)新增两个可选参数 `source_type: str = "manual"`、`source_repo_key: str = ""`,透传给 `store.create_document`。手动上传调用不变(取默认值)。
- **`sync_kb_repo_source` / `sync_kb_repo_source_changes`** 调用 `add_document` 时传 `source_type="git"`, `source_repo_key=repo_key`。
- **`store.create_document`**(`storage/repositories/knowledge.py`)接收并写入这两列。

### diff 匹配口径(slug + repo_key)

定时同步时,对某个 `(kb_id, repo_key)`:
- `existing = {slug: content_hash}` —— 该 KB 下 `source_type='git' AND source_repo_key=repo_key AND status='active'` 的文档
- `current = {slug: content_hash}` —— 扫描本地仓库得到,slug 由文件 stem 经 `make_slug` 生成(与 `add_document` 一致)
- 以**实际存入的 slug** 为准做比对(而非文件路径),同名文件在不同目录映射到同一 slug,与现状一致

---

## 设计第 2 段:删除流程

### 调用链

```
前端(二次确认) → POST /kbs/{kb_slug}/repo-sources/{repo_key}/delete
  → service.delete_kb_repo_source(actor, kb_slug, repo_key)
```

### `delete_kb_repo_source`(新增 service 方法,`app/service.py`)

严格遵循现有 `delete_document`(`service.py:379-394`)的"**先生成 delete 任务,再软删**"顺序——`sync_jobs` 的 FK 是 `ON DELETE CASCADE`(`schema.py:84`),硬删会销毁任务导致远程后端副本永远清不掉;soft_delete 只翻 status,行和任务都存活。

1. 鉴权:`_require_kb_admin_visible(actor, kb_slug)`
2. 查关联:`store.get_kb_repo_source(kb_id, repo_key)`,不存在则 `NotFound`
3. 找目标文档:`store.list_git_docs_for_repo(kb_id, repo_key)`(新增,条件 `source_type='git' AND source_repo_key=repo_key AND status='active'`)
4. **逐个文档**循环调用 `self.delete_document(actor, slug, later=True)`:
   - 它内部:遍历该文档关联 KB 的 active backend targets → 生成 `Operation.delete` sync_job → `soft_delete_document`
   - `later=True` 表示只入队不立即 drain(由调用方/定时器统一 drain)
5. **解绑**:`store.delete_kb_repo_source(kb_id, repo_key)`(新增,设 `status='inactive'`,遵循 `kb_repo_sources` 既有 status 列的软删惯例)
6. 返回:`{kb_slug, repo_key, deleted_docs: N}`

### store 层新增(`storage/repositories/knowledge.py`)

- `delete_kb_repo_source(kb_id, repo_key)`:`UPDATE kb_repo_sources SET status='inactive', updated_at=? WHERE kb_id=? AND repo_key=?`
- `list_git_docs_for_repo(kb_id, repo_key)`:返回 `[{id, slug, content_hash, ...}]`,按 `source_type='git' AND source_repo_key=? AND status='active'` 过滤

### 列表接口增强(供确认文案)

`list_kb_repo_sources(kb_id)`(`knowledge.py:646-658`)返回的每个 source 增加 `doc_count` 字段——该 KB 下 `source_type='git' AND source_repo_key=key AND status='active'` 的文档数(子查询或 LEFT JOIN 统计)。

### 删除二次确认文案

```
确定移除数据源「{repo_name}」？将从该知识库删除 {doc_count} 个由它提供的文档，并在后端同步删除。此操作不会删除 git 仓库本身。
```

---

## 设计第 3 段:定时同步与 diff(核心)

### 落地位置:改造 `DocSyncScheduler`

采用**方案 A**(改造现有调度器,不新增 cron)。`doc_sync_scheduler.py:_run_sync`(`src/agent_bridge/knowledge_management/docs_knowledge/doc_sync_scheduler.py:50`)改为两阶段:

```python
def _run_sync(self) -> None:
    ...
    try:
        self._sync_repo_sources()   # 新增:git diff → 生成 create/delete 任务
        result = self._service.sync(admin, all_users=True, ...)  # 现有:drain sync_jobs
        ...
```

**失败隔离**:`_sync_repo_sources` 内部对每个源 try/except,出错调 `store.mark_kb_repo_source_sync(kb_id, repo_key, success=False, error=...)`,跳过该源,**不抛出**,不影响后续 `service.sync()` drain。即使所有 git 源都失败,已积压的 create/delete 任务照常 drain。

### `_sync_repo_sources`(新增 scheduler 方法)

需跨所有 KB 枚举,store 层新增 `list_all_active_repo_sources()`(当前只有 per-kb 的 `list_kb_repo_sources`)。该方法返回 `[{kb_id, kb_slug, repo_key, ...}]`——**必须带出 `kb_slug`**,因为 service 方法约定用 `kb_slug` 定位 KB(与现有 `sync_kb_repo_source` 一致),而 JOIN `knowledge_bases` 取 slug 成本极低:

```python
def _sync_repo_sources(self) -> None:
    admin = next(iter(self._admins), "root")
    for src in self._store.list_all_active_repo_sources():
        kb_id, kb_slug, repo_key = src["kb_id"], src["kb_slug"], src["repo_key"]
        try:
            self._service.sync_kb_repo_source_changes(admin, kb_slug, repo_key)
        except Exception as exc:
            self._store.mark_kb_repo_source_sync(kb_id, repo_key, success=False, error=str(exc))
            logger.warning("git 源同步失败 kb=%s repo=%s: %s", kb_slug, repo_key, exc)
```

只取 `kb_repo_sources.status='active'` 的源,解绑(`status='inactive'`)后自然不再处理。

### `sync_kb_repo_source_changes`(新增 service 方法,`app/service.py`)

这是 diff 的实现,与现有 `sync_kb_repo_source`(全量重新添加)并列。

```
1. 确保 git 镜像最新:复用 codegraph.sync_repository(actor, repo_key)
   (内部 git fetch + reset --hard origin/<branch>,已存在)
2. 读取已导入文档: existing = {slug: content_hash}
   store.list_git_docs_for_repo(kb_id, repo_key)
3. 扫描仓库:遍历 local_path.rglob("*"),过滤规则同现有 sync_kb_repo_source
   (跳过 symlink / .git/ 目录, 后缀 ∈ include_suffixes ∩ ALLOWED_EXTENSIONS)
   计算每个文件的 (slug, content_hash)
4. 三向 diff:
   - 新增: current 有 existing 无       → add_document(source_type="git", later=True)
   - 删除: existing 有 current 无       → delete_document(slug, later=True)
   - 修改: 都有但 hash 不同             → delete_document(旧) + add_document(新)
   - 不变: hash 相同                    → 跳过
5. mark_kb_repo_source_sync(success=True)
6. 返回 {added, removed, updated, unchanged}
```

### 与现有 `sync_kb_repo_source` 的关系(语义统一)

现有手动同步 `sync_kb_repo_source`(`service.py:474`)目前是**全量重新添加**(产生重复)。**改为内部调用 `sync_kb_repo_source_changes`**——手动点"立即同步"也变成增量,消除现有的重复文档坑,行为统一。返回值从 `{matched, imported, skipped}` 改为 `{added, removed, updated, unchanged}`。

### content_hash 来源

`existing` 的 content_hash 从 `document_versions.content_hash`(文档当前 version)取。`current` 的 content_hash 对新文件计算(与 `archive.store` 同算法)。只在判定为"新增/修改"时才真正 `add_document`(它内部再 archive 一次,内容相同命中内容寻址去重,不重复存盘)。

---

## 设计第 4 段:API 与前端

### 后端 API

**新增删除路由**(`api/routes/knowledge.py`,遵循 house style 用 `POST .../delete`):

```python
@router.post("/kbs/{kb_slug}/repo-sources/{repo_key}/delete")
def delete_kb_repo_source(kb_slug: str, repo_key: str, current_actor=Depends(actor)):
    return service.delete_kb_repo_source(current_actor, kb_slug, repo_key)
```

**列表接口增强**:`GET /kbs/{kb_slug}/repo-sources` 每个 source 带 `doc_count`。

**同步返回值变更**:`POST .../sync` 返回 `{added, removed, updated, unchanged}`。

### 前端(`KnowledgeView.vue`)

**数据源列表表格**(lines 786-812)操作列:在现有"立即同步"按钮旁加"删除"按钮。遵循现有 `deleteKb`(`:100-108`)/ `deleteDoc`(`:192-199`)的 native `confirm()` 二次确认模式:

```js
async function deleteRepoSource(source: KbRepoSource) {
  if (!confirm(`确定移除数据源「${source.repo_name}」？将从该知识库删除 ${source.doc_count} 个由它提供的文档，并在后端同步删除。此操作不会删除 git 仓库本身。`)) return
  try {
    await api.deleteKbRepoSource(detailKb.value.slug, source.repo_key)
    detailRepoSources.value = await api.listKbRepoSources(detailKb.value.slug)
  } catch (e: any) {
    alert(e.message || '删除失败')
  }
}
```

**同步结果文案**:`syncRepoSource`(`:266-285`)里 `已导入 N 个文件` 改为 `已同步：新增 ${r.added}，删除 ${r.removed}，更新 ${r.updated}`。

**加载态**:新增 per-key 的 `repoSourceDeleting` ref,删除时按钮禁用 + 文案"删除中..."。

**API client**(`api/client.ts:413-418`)新增:
```ts
deleteKbRepoSource: (kbSlug: string, repoKey: string) => post(`/kbs/${kbSlug}/repo-sources/${repoKey}/delete`),
```
`KbRepoSource` 类型加 `doc_count: number`;`syncKbRepoSource` 返回类型适配新字段。

### 配置面

**不新增 cron 列**——复用现有 `doc_sync_cron`(默认 `*/30 * * * *`)。git diff 作为 `DocSyncScheduler._run_sync` 的前置步骤,频率跟随 doc_sync。配置页无需新 UI。

---

## 改动范围汇总

| 层 | 改动 |
|----|------|
| **Schema** | `documents` 加 `source_type` + `source_repo_key`;`migrate_phase2` 双写 |
| **Store** | `list_all_active_repo_sources`、`delete_kb_repo_source`、`list_git_docs_for_repo`;`list_kb_repo_sources` 加 `doc_count`;`create_document` 写 source 列 |
| **Service** | `add_document` 加 source 参数;新增 `sync_kb_repo_source_changes`(diff);新增 `delete_kb_repo_source`;`sync_kb_repo_source` 改为调 diff |
| **Scheduler** | `DocSyncScheduler._run_sync` 加 `_sync_repo_sources` 前置阶段 |
| **API** | 新增 `POST .../repo-sources/{key}/delete`;列表返回加 `doc_count`;sync 返回值变更 |
| **前端** | 删除按钮 + 二次确认;同步文案适配;client 加 `deleteKbRepoSource`;类型加 `doc_count` |

## 测试策略

- **store 层**:`list_all_active_repo_sources`(含 `kb_slug` JOIN)、`delete_kb_repo_source`、`list_git_docs_for_repo`、`doc_count` 统计
- **service 层**:`sync_kb_repo_source_changes` 的 diff 四分支(新增/删除/修改=先删后加/不变);`delete_kb_repo_source` 的任务生成 + 软删顺序、解绑
  - **修改分支单独测**:验证先 `Operation.delete` 旧文档 + 软删,再 `add_document` 新文档,两者 doc_id 不同
- **migration 回归**:`tests/test_kb_defaults_migration.py` 风格——建库、`DROP COLUMN` documents 的 source 列、跑 `migrate_phase2()`、断言恢复且能写入
- **scheduler**:模拟 `_run_sync` 两阶段;单源失败不影响后续 drain
- **API/前端**:删除路由鉴权与返回;现有 `tests/test_capability_api.py:594`(`test_kb_repo_source_api_saves_config_and_syncs_filtered_files`)需更新断言(返回值字段从 `matched/imported/skipped` 变为 `added/removed/updated/unchanged`)

## 非目标(YAGNI)

- 不做存量文档来源回填(已确认接受首次重复)
- 不新增独立 cron / cron 配置项(复用 doc_sync_cron)
- 不引入文件路径级 diff(只按 slug 匹配)
- 不删除 `code_repositories` 记录和本地克隆(其他 KB 可能引用)
- 不做"无其他引用时清理镜像"的额外清理(后续可加)
