# Git 数据源删除与增量同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库的 git 数据源支持"删除(二次确认 + 清理文档 + 生成删除同步任务)"和"定时增量同步(新增/删除/修改各自生成同步任务)"。

**Architecture:** 在 `documents` 表加 `source_type`/`source_repo_key` 两列追踪文档来源;service 层新增 diff 方法(`sync_kb_repo_source_changes`)替代现有全量重添加;改造 `DocSyncScheduler._run_sync` 在 drain 前串行执行 git diff;新增 `delete_kb_repo_source` 遵循"先生成 Operation.delete 任务再 soft_delete"的现有删除模式。

**Tech Stack:** Python 3.11 / FastAPI / SQLite / APScheduler / Vue 3 + TypeScript。无新依赖。

**Spec:** `docs/superpowers/specs/2026-07-01-git-source-sync-delete-design.md`

---

### Task 1: Schema — documents 表加 source 列

**Files:**
- Modify: `src/agent_bridge/storage/schema.py:25-35`
- Modify: `src/agent_bridge/storage/sqlite.py:89` (migrate_phase2)

- [ ] **Step 1: 写 migration 回归测试(failing)**

在 `tests/test_kb_defaults_migration.py` 末尾追加(参考该文件已有测试风格——建库、DROP COLUMN、跑 migrate_phase2、断言恢复):

```python
def test_migrate_phase2_adds_documents_source_columns(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    with store.connect() as conn:
        conn.execute("ALTER TABLE documents DROP COLUMN source_type")
        conn.execute("ALTER TABLE documents DROP COLUMN source_repo_key")
    # 重新迁移
    store.migrate_phase2()
    with store.connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        assert "source_type" in cols
        assert "source_repo_key" in cols
        # 默认值正确
        conn.execute(
            "INSERT INTO documents (slug, title, owner_user) VALUES ('t', 'T', 'root')"
        )
        row = conn.execute("SELECT source_type, source_repo_key FROM documents WHERE slug='t'").fetchone()
        assert row[0] == "manual"
        assert row[1] == ""
```

确保 `tests/test_kb_defaults_migration.py` 顶部已 `from agent_bridge.storage.sqlite import SQLiteStore`(若已有则跳过)。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_kb_defaults_migration.py::test_migrate_phase2_adds_documents_source_columns -v`
Expected: FAIL — `OperationalError: no such column: source_type` 或 migration 未恢复该列。

- [ ] **Step 3: 在 schema.py 的 CREATE TABLE 加列**

`src/agent_bridge/storage/schema.py:25-35`,在 `deleted_at TEXT` 后(第 34 行后)加两列:

```sql
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_user TEXT NOT NULL,
  current_version_id INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT,
  source_type TEXT NOT NULL DEFAULT 'manual',
  source_repo_key TEXT NOT NULL DEFAULT ''
);
```

- [ ] **Step 4: 在 migrate_phase2 加 _ensure_columns 调用**

`src/agent_bridge/storage/sqlite.py` 的 `migrate_phase2()` 方法内(第 89 行起,`with self.connect() as conn:` 块内,建议紧跟现有 `knowledge_bases` 的 `_ensure_columns` 调用之后,约第 113 行附近)加:

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

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_kb_defaults_migration.py::test_migrate_phase2_adds_documents_source_columns tests/test_storage.py -v`
Expected: PASS(新测试通过,且现有 storage 测试不回归)。

- [ ] **Step 6: 提交**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py tests/test_kb_defaults_migration.py
git commit -m "feat: track document source_type and source_repo_key"
```

---

### Task 2: Store 层 — create_document 写入 source 列

**Files:**
- Modify: `src/agent_bridge/storage/repositories/knowledge.py:129-139`

- [ ] **Step 1: 写 failing 测试**

在 `tests/test_storage.py` 加测试(参考该文件已有 `create_document` 测试的建库样板):

```python
def test_create_document_records_source(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    doc = store.create_document(slug="guide", title="Guide", owner_user="root",
                                source_type="git", source_repo_key="docs-repo")
    assert doc["source_type"] == "git"
    assert doc["source_repo_key"] == "docs-repo"
    # 默认值(不传时)
    doc2 = store.create_document(slug="notes", title="Notes", owner_user="root")
    assert doc2["source_type"] == "manual"
    assert doc2["source_repo_key"] == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_storage.py::test_create_document_records_source -v`
Expected: FAIL — `TypeError: create_document() got an unexpected keyword argument 'source_type'`。

- [ ] **Step 3: 修改 create_document 签名与 INSERT**

`src/agent_bridge/storage/repositories/knowledge.py:129-139` 改为:

```python
    def create_document(
        self,
        slug: str,
        title: str,
        owner_user: str,
        source_type: str = "manual",
        source_repo_key: str = "",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO documents (slug, title, owner_user, source_type, source_repo_key) VALUES (?, ?, ?, ?, ?)",
                (slug, title, owner_user, source_type, source_repo_key),
            )
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)).fetchone()
            document = row_to_dict(row)
            if document is None:
                raise KeyError(f"document not found: {cursor.lastrowid}")
            return document
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_storage.py::test_create_document_records_source -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agent_bridge/storage/repositories/knowledge.py tests/test_storage.py
git commit -m "feat: store document source_type and source_repo_key"
```

---

### Task 3: Store 层 — git 文档查询方法

**Files:**
- Modify: `src/agent_bridge/storage/repositories/knowledge.py`(在 `mark_kb_repo_source_sync` 之前,约第 673 行前新增方法)

需要两个新方法:`list_git_docs_for_repo`(diff 用)和 `list_all_active_repo_sources`(scheduler 跨 KB 枚举用)。

- [ ] **Step 1: 写 failing 测试**

在 `tests/test_storage.py` 加:

```python
def test_list_git_docs_for_repo(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("docs", "Docs", "", "root")
    # 两个 git 文档 + 一个 manual 文档 + 一个已软删的 git 文档
    g1 = store.create_document("guide", "Guide", "root", source_type="git", source_repo_key="r1")
    g2 = store.create_document("notes", "Notes", "root", source_type="git", source_repo_key="r1")
    store.create_document("manual", "Manual", "root")  # 不属于任何 repo
    g_del = store.create_document("old", "Old", "root", source_type="git", source_repo_key="r1")
    store.attach_document_to_kb(g1["id"], kb["id"], "root")
    store.attach_document_to_kb(g2["id"], kb["id"], "root")
    store.attach_document_to_kb(g_del["id"], kb["id"], "root")
    store.soft_delete_document(g_del["id"])
    # 为 g1/g2 建版本带 content_hash
    v1 = store.create_document_version(g1["id"], "guide.md", "hash-a", 10, "text/markdown", "/a", "root")
    store.update_document_current_version(g1["id"], v1["id"])

    result = store.list_git_docs_for_repo(kb["id"], "r1")
    slugs = {d["slug"] for d in result}
    assert slugs == {"guide", "notes"}  # 不含 manual、不含已软删的 old
    guide = next(d for d in result if d["slug"] == "guide")
    assert guide["content_hash"] == "hash-a"
```

注:`create_document_version` 和 `update_document_current_version` 的签名以仓库现有定义为准——若 `update_document_current_version` 不存在,改用直接 SQL `UPDATE documents SET current_version_id=?` 或用 `attach_document_to_kb` 同级的现有方法。先 grep 确认方法名。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_storage.py::test_list_git_docs_for_repo -v`
Expected: FAIL — `AttributeError: 'KnowledgeRepository' object has no attribute 'list_git_docs_for_repo'`。

- [ ] **Step 3: 实现 list_git_docs_for_repo**

在 `src/agent_bridge/storage/repositories/knowledge.py` 的 `mark_kb_repo_source_sync` 方法前(约第 673 行前)加:

```python
    def list_git_docs_for_repo(self, kb_id: int, repo_key: str) -> list[dict[str, Any]]:
        """返回某 KB 下由指定 git 仓库提供、仍 active 的文档(带当前 version 的 content_hash)。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT doc.slug AS slug, doc.id AS id, ver.content_hash AS content_hash
                FROM documents doc
                JOIN document_kbs dk ON dk.doc_id = doc.id
                LEFT JOIN document_versions ver ON ver.id = doc.current_version_id
                WHERE dk.kb_id = ? AND dk.status = 'active'
                  AND doc.source_type = 'git' AND doc.source_repo_key = ?
                  AND doc.status != 'deleted'
                """,
                (kb_id, repo_key),
            ).fetchall()
            return [dict(row) for row in rows]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_storage.py::test_list_git_docs_for_repo -v`
Expected: PASS

- [ ] **Step 5: 写 list_all_active_repo_sources 测试**

在 `tests/test_storage.py` 加:

```python
def test_list_all_active_repo_sources(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb1 = store.create_kb("kb1", "KB1", "", "root")
    kb2 = store.create_kb("kb2", "KB2", "", "root")
    # 先建 code_repository(满足外键)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO code_repositories (repo_key, name, git_url, branch, auth_ref, status) "
            "VALUES ('r1', 'R1', 'http://x', 'main', '', 'active')"
        )
    store.upsert_kb_repo_source(kb1["id"], "r1", [".md"])
    store.upsert_kb_repo_source(kb2["id"], "r1", [".md", ".txt"])
    result = store.list_all_active_repo_sources()
    assert len(result) == 2
    slugs = {(r["kb_slug"], r["repo_key"]) for r in result}
    assert ("kb1", "r1") in slugs
    assert ("kb2", "r1") in slugs
    assert all(r["status"] == "active" for r in result)
```

- [ ] **Step 6: 运行测试确认失败**

Run: `python -m pytest tests/test_storage.py::test_list_all_active_repo_sources -v`
Expected: FAIL — `AttributeError: ... has no attribute 'list_all_active_repo_sources'`。

- [ ] **Step 7: 实现 list_all_active_repo_sources**

在 `list_git_docs_for_repo` 之后加:

```python
    def list_all_active_repo_sources(self) -> list[dict[str, Any]]:
        """跨所有 KB 枚举 active 的 git 数据源(scheduler 定时同步用)。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source.kb_id AS kb_id, kb.slug AS kb_slug,
                       source.repo_key AS repo_key, source.include_suffixes_json AS include_suffixes_json
                FROM kb_repo_sources source
                JOIN knowledge_bases kb ON kb.id = source.kb_id
                WHERE source.status = 'active' AND kb.status = 'active'
                ORDER BY kb.slug, source.repo_key
                """,
            ).fetchall()
            return [self._kb_repo_source_payload(row) for row in rows]
```

注:`_kb_repo_source_payload` 已存在(`knowledge.py:693-702`),会把 `include_suffixes_json` 转成 `include_suffixes`。

- [ ] **Step 8: 运行测试确认通过**

Run: `python -m pytest tests/test_storage.py::test_list_git_docs_for_repo tests/test_storage.py::test_list_all_active_repo_sources -v`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add src/agent_bridge/storage/repositories/knowledge.py tests/test_storage.py
git commit -m "feat: store methods for git doc lookup and repo source enumeration"
```

---

### Task 4: Store 层 — delete_kb_repo_source + list_kb_repo_sources 带 doc_count

**Files:**
- Modify: `src/agent_bridge/storage/repositories/knowledge.py:646-658` (list_kb_repo_sources)
- Modify: `src/agent_bridge/storage/repositories/knowledge.py` (新增 delete_kb_repo_source)

- [ ] **Step 1: 写 failing 测试**

在 `tests/test_storage.py` 加:

```python
def test_list_kb_repo_sources_includes_doc_count(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("docs", "Docs", "", "root")
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO code_repositories (repo_key, name, git_url, branch, auth_ref, status) "
            "VALUES ('r1', 'R1', 'http://x', 'main', '', 'active')"
        )
    store.upsert_kb_repo_source(kb["id"], "r1", [".md"])
    # 两个 git 文档 + 一个 manual
    for slug in ("g1", "g2"):
        d = store.create_document(slug, slug, "root", source_type="git", source_repo_key="r1")
        store.attach_document_to_kb(d["id"], kb["id"], "root")
    d3 = store.create_document("m1", "M1", "root")
    store.attach_document_to_kb(d3["id"], kb["id"], "root")
    result = store.list_kb_repo_sources(kb["id"])
    assert result[0]["doc_count"] == 2


def test_delete_kb_repo_source_soft_deletes(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.init_schema()
    kb = store.create_kb("docs", "Docs", "", "root")
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO code_repositories (repo_key, name, git_url, branch, auth_ref, status) "
            "VALUES ('r1', 'R1', 'http://x', 'main', '', 'active')"
        )
    store.upsert_kb_repo_source(kb["id"], "r1", [".md"])
    store.delete_kb_repo_source(kb["id"], "r1")
    # list 只返回 active
    assert store.list_kb_repo_sources(kb["id"]) == []
    # 但行还在(软删)
    with store.connect() as conn:
        row = conn.execute("SELECT status FROM kb_repo_sources WHERE kb_id=? AND repo_key='r1'", (kb["id"],)).fetchone()
        assert row[0] == "inactive"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_storage.py::test_list_kb_repo_sources_includes_doc_count tests/test_storage.py::test_delete_kb_repo_source_soft_deletes -v`
Expected: FAIL — KeyError `doc_count` / `AttributeError: ... has no attribute 'delete_kb_repo_source'`。

- [ ] **Step 3: 改 list_kb_repo_sources 加 doc_count**

`src/agent_bridge/storage/repositories/knowledge.py:646-658` 改为:

```python
    def list_kb_repo_sources(self, kb_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source.*, repo.name AS repo_name,
                       (
                           SELECT COUNT(*)
                           FROM documents doc
                           JOIN document_kbs dk ON dk.doc_id = doc.id
                           WHERE dk.kb_id = source.kb_id
                             AND dk.status = 'active'
                             AND doc.source_type = 'git'
                             AND doc.source_repo_key = source.repo_key
                             AND doc.status != 'deleted'
                       ) AS doc_count
                FROM kb_repo_sources source
                JOIN code_repositories repo ON repo.repo_key = source.repo_key
                WHERE source.kb_id = ? AND source.status = 'active'
                ORDER BY source.repo_key
                """,
                (kb_id,),
            ).fetchall()
            return [self._kb_repo_source_payload(row) for row in rows]
```

`_kb_repo_source_payload`(`:693-702`)用 `dict(row)` 会自动带上 `doc_count`,无需改。

- [ ] **Step 4: 实现 delete_kb_repo_source**

在 `mark_kb_repo_source_sync` 之后加:

```python
    def delete_kb_repo_source(self, kb_id: int, repo_key: str) -> None:
        """软删除 KB 与 git 仓库的数据源关联(保留行,供历史/重建)。"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE kb_repo_sources
                SET status = 'inactive', updated_at = CURRENT_TIMESTAMP
                WHERE kb_id = ? AND repo_key = ?
                """,
                (kb_id, repo_key),
            )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_storage.py::test_list_kb_repo_sources_includes_doc_count tests/test_storage.py::test_delete_kb_repo_source_soft_deletes -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/agent_bridge/storage/repositories/knowledge.py tests/test_storage.py
git commit -m "feat: soft-delete kb repo source and count git docs per source"
```

---

### Task 5: Service 层 — add_document 加 source 参数

**Files:**
- Modify: `src/agent_bridge/app/service.py:261-305`

说明:`AgentBridgeService` 构造较重(装配十余个子服务),测试不便直接 new。本任务的验证并入 Task 6 的同步测试(同步后断言导入文档的 `source_type='git'`),本任务只做实现 + 既有测试不回归。

- [ ] **Step 1: 改 add_document 签名与 create_document 调用**

`src/agent_bridge/app/service.py:261-305`,`add_document` 签名加两参数,并透传给 `create_document`(第 278 行):

```python
    def add_document(
        self,
        actor: str,
        source: Path,
        kb_slugs: list[str],
        later: bool,
        original_filename: str | None = None,
        source_type: str = "manual",
        source_repo_key: str = "",
    ) -> dict[str, Any]:
```

第 278 行 `doc = self.store.create_document(slug=slug, title=..., owner_user=actor)` 改为:

```python
        doc = self.store.create_document(
            slug=slug, title=Path(display_name).stem, owner_user=actor,
            source_type=source_type, source_repo_key=source_repo_key,
        )
```

(其余行不变。)

- [ ] **Step 2: 运行既有测试确认不回归**

Run: `python -m pytest tests/test_capability_api.py tests/test_storage.py -v`
Expected: 全绿(参数有默认值,既有调用不受影响)。`source_type` 的写入断言在 Task 6 同步测试里覆盖。

- [ ] **Step 3: 提交**

```bash
git add src/agent_bridge/app/service.py
git commit -m "feat: add_document accepts source_type and source_repo_key"
```

---

### Task 6: Service 层 — sync_kb_repo_source_changes(diff 核心)

**Files:**
- Modify: `src/agent_bridge/app/service.py`(在 `sync_kb_repo_source` 之后,约第 519 行后新增方法)

这是整个功能的核心。实现三向 diff:新增→add,删除→delete,修改→先删后加。

- [ ] **Step 1: 写 failing 测试 — 新增分支**

在 `tests/test_capability_api.py` 加(复用 Task 中 `_git_repo` 辅助函数;若不存在,grep 确认其名):

```python
def _setup_repo_and_kb(tmp_path, client):
    repo = _git_repo(tmp_path / "repo")
    (repo / "guide.md").write_text("# Guide\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    client.post("/kbs", json={"slug":"docs","name":"Docs","description":""}, headers={"X-Agent-Bridge-User":"root"})
    client.post("/code-repo/repositories", json={"repo_key":"r1","name":"R1","git_url":str(repo),"branch":"master"}, headers={"X-Agent-Bridge-User":"root"})
    client.post("/code-repo/repositories/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    client.post("/kbs/docs/repo-sources", json={"repo_key":"r1","include_suffixes":[".md"]}, headers={"X-Agent-Bridge-User":"root"})
    return repo


def test_sync_changes_imports_new_files(wm_paths, tmp_path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    # 首次同步:guide.md 是新文件
    r = client.post("/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    assert r.json()["removed"] == 0
    assert r.json()["updated"] == 0
    docs = client.get("/docs?kb=docs", headers={"X-Agent-Bridge-User":"root"}).json()
    assert {d["title"] for d in docs} == {"guide"}
```

> 说明:来源字段(`source_type`/`source_repo_key`)的写入正确性由 Task 2 的 store 单测(create_document 带参数)和 Task 5(add_document 透传)共同覆盖。docs 列表 API 的 `list_docs_for_kb` SELECT 暂不带 source 字段,无需在此扩展。
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_capability_api.py::test_sync_changes_imports_new_files -v`
Expected: FAIL — 返回字段还是旧的 `matched/imported/skipped`,`added` 不存在。

- [ ] **Step 3: 实现 sync_kb_repo_source_changes**

在 `src/agent_bridge/app/service.py` 的 `sync_kb_repo_source` 方法之后新增:

```python
    def sync_kb_repo_source_changes(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """增量同步:对比仓库文件与已导入文档,生成 create/delete 同步任务。

        diff 口径:按 slug + repo_key 匹配。
        - 新增文件 → add_document(source_type='git')
        - 仓库已删除 → delete_document(先生成 Operation.delete 任务再 soft_delete)
        - 内容修改 → 先删后加(doc_id 变化)
        - 内容不变 → 跳过
        """
        kb = self._require_kb_admin_visible(actor, kb_slug)
        source = self.store.get_kb_repo_source(kb["id"], repo_key)
        if source is None:
            raise NotFound("knowledge repo source not found")
        repo = self.store.get_code_repository(repo_key)
        if repo is None:
            raise NotFound("code repository not found")

        local_path = Path(str(repo.get("local_path") or "")) if repo.get("local_path") else self.paths.repos_dir / repo_key
        try:
            if not local_path.exists():
                self.codegraph.sync_repository(actor, repo_key)
                repo = self.store.get_code_repository(repo_key) or repo
                local_path = Path(str(repo.get("local_path") or "")) if repo.get("local_path") else self.paths.repos_dir / repo_key
            if not local_path.exists():
                raise ValidationError("code repository has not been synced")

            suffixes = set(source["include_suffixes"])
            # existing: {slug: content_hash}
            existing = {d["slug"]: (d.get("content_hash") or "") for d in self.store.list_git_docs_for_repo(kb["id"], repo_key)}
            existing_slugs = set(existing.keys())

            # current: 扫描仓库,计算每个文件的 (slug, content_hash)
            current: dict[str, str] = {}
            for path in sorted(local_path.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    relative_parts = path.relative_to(local_path).parts
                except ValueError:
                    continue
                if ".git" in relative_parts:
                    continue
                if path.suffix.lower() not in suffixes:
                    continue
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                slug = make_slug(Path(path.name).stem)
                current[slug] = self._sha256_file(path)

            added = removed = updated = unchanged = 0
            # 新增 + 修改
            for slug, content_hash in current.items():
                if slug not in existing_slugs:
                    # 找到对应文件路径(按 slug 反查;同名取第一个)
                    self._import_repo_file(actor, kb_slug, repo_key, local_path, suffixes, slug)
                    added += 1
                elif existing[slug] != content_hash:
                    # 修改:先删后加
                    self.delete_document(actor, slug, later=True)
                    self._import_repo_file(actor, kb_slug, repo_key, local_path, suffixes, slug)
                    updated += 1
                else:
                    unchanged += 1
            # 删除
            for slug in existing_slugs - set(current.keys()):
                self.delete_document(actor, slug, later=True)
                removed += 1

            self.store.mark_kb_repo_source_sync(kb["id"], repo_key, success=True)
            return {"kb_slug": kb_slug, "repo_key": repo_key,
                    "added": added, "removed": removed, "updated": updated, "unchanged": unchanged}
        except Exception as exc:
            self.store.mark_kb_repo_source_sync(kb["id"], repo_key, success=False, error=str(exc))
            raise
```

并新增两个辅助方法(紧跟其后):

```python
    def _import_repo_file(self, actor: str, kb_slug: str, repo_key: str,
                          local_path: Path, suffixes: set[str], target_slug: str) -> None:
        """按 slug 找到仓库内第一个匹配文件并导入为 git 文档。"""
        from agent_bridge.core.slug import make_slug as _make_slug
        for path in sorted(local_path.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative_parts = path.relative_to(local_path).parts
            except ValueError:
                continue
            if ".git" in relative_parts:
                continue
            if path.suffix.lower() not in suffixes or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            if _make_slug(Path(path.name).stem) == target_slug:
                self.add_document(actor, path, [kb_slug], later=True,
                                  original_filename=path.name,
                                  source_type="git", source_repo_key=repo_key)
                return

    @staticmethod
    def _sha256_file(path: Path) -> str:
        import hashlib
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
```

- [ ] **Step 4: 改 sync_kb_repo_source 改为调用 diff**

`src/agent_bridge/app/service.py:474-518` 的 `sync_kb_repo_source` 方法整体替换为薄包装(手动同步也走增量 diff,消除重复):

```python
    def sync_kb_repo_source(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """手动同步:转发到增量 diff 逻辑(行为与定时同步一致)。"""
        return self.sync_kb_repo_source_changes(actor, kb_slug, repo_key)
```

- [ ] **Step 5: 运行新增测试确认通过**

Run: `python -m pytest tests/test_capability_api.py::test_sync_changes_imports_new_files -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/agent_bridge/app/service.py tests/test_capability_api.py
git commit -m "feat: incremental git source sync via slug-based diff"
```

---

### Task 7: Service 层 — diff 的删除/修改分支测试

**Files:**
- Test: `tests/test_capability_api.py`

补充 diff 的另两个分支,确保修改=先删后加且 doc_id 变化、删除分支正确。

- [ ] **Step 1: 写修改分支测试**

```python
def test_sync_changes_modifies_changed_file_as_delete_then_add(wm_paths, tmp_path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    # 首次导入 guide.md
    client.post("/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    docs_before = client.get("/docs?kb=docs", headers={"X-Agent-Bridge-User":"root"}).json()
    doc_id_before = docs_before[0]["id"]
    # 修改文件内容
    (repo / "guide.md").write_text("# Guide v2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=repo, check=True, capture_output=True)
    client.post("/code-repo/repositories/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    # 再次同步:应为 updated=1
    r = client.post("/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    assert r.json()["updated"] == 1
    assert r.json()["added"] == 0
    docs_after = client.get("/docs?kb=docs", headers={"X-Agent-Bridge-User":"root"}).json()
    # doc_id 变化(先删后加)
    assert docs_after[0]["id"] != doc_id_before
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python -m pytest tests/test_capability_api.py::test_sync_changes_modifies_changed_file_as_delete_then_add -v`
Expected: PASS(Task 6 已实现该分支)

- [ ] **Step 3: 写删除分支测试**

```python
def test_sync_changes_removes_deleted_file(wm_paths, tmp_path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    client.post("/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    # 删除文件
    (repo / "guide.md").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "del"], cwd=repo, check=True, capture_output=True)
    client.post("/code-repo/repositories/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    r = client.post("/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    assert r.json()["removed"] == 1
    # active 文档应已清空(guide 被软删)
    docs = client.get("/docs?kb=docs", headers={"X-Agent-Bridge-User":"root"}).json()
    assert docs == []
    # 应生成 delete 同步任务
    jobs = client.get("/sync/status", headers={"X-Agent-Bridge-User":"root"}).json()["jobs"]
    assert any(j["operation"] == "delete" for j in jobs)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_capability_api.py::test_sync_changes_removes_deleted_file -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_capability_api.py
git commit -m "test: cover diff modify and remove branches for git source sync"
```

---

### Task 8: Service 层 — delete_kb_repo_source

**Files:**
- Modify: `src/agent_bridge/app/service.py`(在 `sync_kb_repo_source` 附近新增方法)

- [ ] **Step 1: 写 failing 测试**

```python
def test_delete_kb_repo_source_removes_docs_and_generates_delete_jobs(wm_paths, tmp_path) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    client = TestClient(app)
    repo = _setup_repo_and_kb(tmp_path, client)
    # 导入两个 git 文档
    client.post("/kbs/docs/repo-sources/r1/sync", headers={"X-Agent-Bridge-User":"root"})
    # 删除数据源
    r = client.post("/kbs/docs/repo-sources/r1/delete", headers={"X-Agent-Bridge-User":"root"})
    assert r.status_code == 200, r.text
    assert r.json()["deleted_docs"] == 1
    # active 文档清空
    docs = client.get("/docs?kb=docs", headers={"X-Agent-Bridge-User":"root"}).json()
    assert docs == []
    # 生成 delete 同步任务
    jobs = client.get("/sync/status", headers={"X-Agent-Bridge-User":"root"}).json()["jobs"]
    assert any(j["operation"] == "delete" for j in jobs)
    # 数据源已解绑(list 为空)
    sources = client.get("/kbs/docs/repo-sources", headers={"X-Agent-Bridge-User":"root"}).json()
    assert sources == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_capability_api.py::test_delete_kb_repo_source_removes_docs_and_generates_delete_jobs -v`
Expected: FAIL — `404` 或路由不存在(`/repo-sources/r1/delete` 未定义)。

- [ ] **Step 3: 实现 service 方法**

在 `src/agent_bridge/app/service.py` 的 `sync_kb_repo_source` 之后新增:

```python
    def delete_kb_repo_source(self, actor: str, kb_slug: str, repo_key: str) -> dict[str, Any]:
        """删除 KB 的 git 数据源:解绑关联 + 软删除该 repo 提供的文档 + 生成 delete 同步任务。

        遵循 delete_document 的顺序:先生成 Operation.delete 任务再 soft_delete。
        保留 code_repositories 记录和本地克隆(其他 KB 可能引用)。
        """
        kb = self._require_kb_admin_visible(actor, kb_slug)
        source = self.store.get_kb_repo_source(kb["id"], repo_key)
        if source is None:
            raise NotFound("knowledge repo source not found")
        git_docs = self.store.list_git_docs_for_repo(kb["id"], repo_key)
        for doc in git_docs:
            self.delete_document(actor, doc["slug"], later=True)
        self.store.delete_kb_repo_source(kb["id"], repo_key)
        logger.info("git 数据源已删除 kb=%s repo=%s 删除文档数=%d", kb_slug, repo_key, len(git_docs))
        return {"kb_slug": kb_slug, "repo_key": repo_key, "deleted_docs": len(git_docs)}
```

- [ ] **Step 4: 暂不运行测试(依赖 Task 9 的路由)**

Task 8 的测试走 API(`/repo-sources/r1/delete`),而路由在 Task 9 才加。先继续 Task 9 Step 1-2 加路由,再回来跑本测试。

- [ ] **Step 5: 提交(与 Task 9 合并提交或单独提交)**

```bash
git add src/agent_bridge/app/service.py tests/test_capability_api.py
git commit -m "feat: delete kb repo source with doc cleanup and delete jobs"
```

---

### Task 9: API 路由 — DELETE 端点

**Files:**
- Modify: `src/agent_bridge/api/routes/knowledge.py:96-98` 之后

- [ ] **Step 1: 加删除路由**

`src/agent_bridge/api/routes/knowledge.py` 在 `sync_kb_repo_source` 路由(第 96-98 行)之后加:

```python
    @router.post("/kbs/{kb_slug}/repo-sources/{repo_key}/delete")
    def delete_kb_repo_source(kb_slug: str, repo_key: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.delete_kb_repo_source(current_actor, kb_slug, repo_key)
```

- [ ] **Step 2: 运行 Task 8 的测试确认通过**

Run: `python -m pytest tests/test_capability_api.py::test_delete_kb_repo_source_removes_docs_and_generates_delete_jobs -v`
Expected: PASS

- [ ] **Step 3: 更新现有 git 源 API 测试断言(返回值字段变更)**

`tests/test_capability_api.py` 的 `test_kb_repo_source_api_saves_config_and_syncs_filtered_files`(约第 594 行),把:
```python
    assert synced.json()["matched"] == 2
```
改为:
```python
    assert synced.json()["added"] == 2
    assert synced.json()["removed"] == 0
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_capability_api.py::test_kb_repo_source_api_saves_config_and_syncs_filtered_files tests/test_capability_api.py::test_delete_kb_repo_source_removes_docs_and_generates_delete_jobs -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agent_bridge/api/routes/knowledge.py tests/test_capability_api.py
git commit -m "feat: POST /repo-sources/{key}/delete endpoint"
```

---

### Task 10: Scheduler — DocSyncScheduler 前置 git diff 阶段

**Files:**
- Modify: `src/agent_bridge/knowledge_management/docs_knowledge/doc_sync_scheduler.py:50-91`

- [ ] **Step 1: 写 failing 测试 — 两阶段顺序 + 单源失败不阻塞 drain**

参考现有 `tests/test_scheduler_progress.py` 的 mock 模式(用 fake `Service` + fake `Store`,直接调 `_run_sync()`)。在该文件追加两个测试:

```python
class _RepoSourceStore(_SyncConfigStore):
    """fake store:list 两个源,一个会触发 service 异常;记录 mark_kb_repo_source_sync 调用。"""
    def __init__(self):
        self.marked_errors: list[tuple[str, str, str]] = []  # (kb_id, repo_key, error)

    def list_all_active_repo_sources(self) -> list[dict[str, Any]]:
        return [
            {"kb_id": 1, "kb_slug": "kb-ok", "repo_key": "r-ok"},
            {"kb_id": 2, "kb_slug": "kb-bad", "repo_key": "r-bad"},
        ]

    def mark_kb_repo_source_sync(self, kb_id, repo_key, *, success, error=None):
        if not success:
            self.marked_errors.append((kb_id, repo_key, error))


def test_run_sync_runs_git_diff_phase_before_drain() -> None:
    calls: list[str] = []

    class Service:
        def sync_kb_repo_source_changes(self, actor, kb_slug, repo_key):
            calls.append(f"diff:{kb_slug}:{repo_key}")
        def sync(self, actor, all_users, progress_callback):
            calls.append("drain")
            return {"processed": 0, "succeeded": 0, "failed": 0}

    store = _RepoSourceStore()
    scheduler = DocSyncScheduler(Service(), store, {"root"})
    scheduler._run_sync()
    # git diff 阶段先于 drain
    assert calls.index("diff:kb-ok:r-ok") < calls.index("drain")
    assert calls.index("diff:kb-bad:r-bad") < calls.index("drain")


def test_run_sync_isolates_failing_source_and_still_drains() -> None:
    class Service:
        def sync_kb_repo_source_changes(self, actor, kb_slug, repo_key):
            if repo_key == "r-bad":
                raise RuntimeError("boom")
        def sync(self, actor, all_users, progress_callback):
            return {"processed": 0, "succeeded": 0, "failed": 0}

    store = _RepoSourceStore()
    scheduler = DocSyncScheduler(Service(), store, {"root"})
    scheduler._run_sync()
    # 失败源被记录,不影响整体 status(仍 succeeded)
    assert (2, "r-bad", "boom") in store.marked_errors
    assert scheduler.get_status()["last_run"]["status"] == "succeeded"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_doc_sync_scheduler.py -v`(或对应文件)
Expected: FAIL — `_sync_repo_sources` 不存在。

- [ ] **Step 3: 在 DocSyncScheduler 加 _sync_repo_sources 与 _run_sync 改造**

`src/agent_bridge/knowledge_management/docs_knowledge/doc_sync_scheduler.py` 的 `_run_sync` 方法(第 50 行起),在 `self._service.sync(...)` 调用(第 66 行)**之前**插入 git diff 阶段:

```python
    def _run_sync(self) -> None:
        admin = next(iter(self._admins), "root")
        logger.info("DocSync 定时同步开始 actor=%s", admin)
        self._current_run = {
            "status": "running",
            "started_at": now_iso(),
            "finished_at": None,
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "current_job": None,
            "error": None,
        }
        try:
            self._sync_repo_sources()   # 新增:git diff → 生成 create/delete 任务
            result = self._service.sync(admin, all_users=True, progress_callback=self._update_progress)
            # ...其余 result 更新逻辑不变...
```

并新增 `_sync_repo_sources` 方法(类内):

```python
    def _sync_repo_sources(self) -> None:
        """git 数据源增量同步:遍历所有 active 源,diff 生成同步任务。

        单源失败仅记录 last_error 并跳过,不阻塞后续 service.sync drain。
        """
        admin = next(iter(self._admins), "root")
        for src in self._store.list_all_active_repo_sources():
            kb_id = src["kb_id"]
            kb_slug = src.get("kb_slug")
            repo_key = src["repo_key"]
            try:
                self._service.sync_kb_repo_source_changes(admin, kb_slug, repo_key)
            except Exception as exc:
                self._store.mark_kb_repo_source_sync(kb_id, repo_key, success=False, error=str(exc))
                logger.warning("git 源同步失败 kb=%s repo=%s: %s", kb_slug, repo_key, exc)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_doc_sync_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agent_bridge/knowledge_management/docs_knowledge/doc_sync_scheduler.py tests/
git commit -m "feat: run git source diff before doc sync drain"
```

---

### Task 11: 前端 — 类型 + API client

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts:527-546`
- Modify: `frontend/capabilities/src/api/client.ts:440-445`

- [ ] **Step 1: 更新 KbRepoSource 类型加 doc_count**

`frontend/capabilities/src/api/types.ts:527-538`,在 `updated_at` 后加 `doc_count`:

```typescript
export interface KbRepoSource {
  id: number
  kb_id: number
  repo_key: string
  repo_name: string
  include_suffixes: string[]
  status: string
  last_synced_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
  doc_count: number
}
```

并改 `KbRepoSourceSyncResult`(`:540-546`)返回字段:

```typescript
export interface KbRepoSourceSyncResult {
  kb_slug: string
  repo_key: string
  added: number
  removed: number
  updated: number
  unchanged: number
}
```

- [ ] **Step 2: 加 deleteKbRepoSource client 方法**

`frontend/capabilities/src/api/client.ts:444-445` 之后加:

```typescript
  deleteKbRepoSource: (kbSlug: string, repoKey: string) =>
    post<{ kb_slug: string; repo_key: string; deleted_docs: number }>(`/kbs/${kbSlug}/repo-sources/${repoKey}/delete`),
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend/capabilities && npm run typecheck`(或 `npx tsc --noEmit`;以项目实际脚本为准,先看 package.json 的 scripts)
Expected: 无类型错误

- [ ] **Step 4: 提交**

```bash
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts
git commit -m "feat(api): add deleteKbRepoSource and sync result types"
```

---

### Task 12: 前端 — 删除按钮 + 二次确认 + 同步文案适配

**Files:**
- Modify: `frontend/capabilities/src/views/knowledge/KnowledgeView.vue:30-36`(state)
- Modify: `frontend/capabilities/src/views/knowledge/KnowledgeView.vue:266-286`(syncRepoSource)
- Modify: `frontend/capabilities/src/views/knowledge/KnowledgeView.vue:806-810`(模板操作列)

- [ ] **Step 1: 加删除加载态 ref**

`KnowledgeView.vue` 约 34 行(`repoSourceSyncing` 附近)加:

```typescript
const repoSourceDeleting = ref<Record<string, boolean>>({})
```

- [ ] **Step 2: 加 deleteRepoSource 处理函数**

在 `syncRepoSource` 函数(约第 286 行)之后加:

```typescript
async function deleteRepoSource(source: KbRepoSource) {
  if (!detailKb.value) return
  if (!confirm(`确定移除数据源「${source.repo_name || source.repo_key}」？将从该知识库删除 ${source.doc_count} 个由它提供的文档，并在后端同步删除。此操作不会删除 git 仓库本身。`)) return
  repoSourceError.value = ''
  repoSourceMessage.value = ''
  repoSourceDeleting.value = { ...repoSourceDeleting.value, [source.repo_key]: true }
  try {
    await api.deleteKbRepoSource(detailKb.value.slug, source.repo_key)
    const [repoSources, docs] = await Promise.all([
      api.listKbRepoSources(detailKb.value.slug),
      api.listDocs(detailKb.value.slug),
    ])
    detailRepoSources.value = repoSources
    detailDocs.value = docs
    repoSourceMessage.value = '已移除数据源'
  } catch (e: any) {
    repoSourceError.value = e.message || '删除失败'
  }
  repoSourceDeleting.value = { ...repoSourceDeleting.value, [source.repo_key]: false }
}
```

- [ ] **Step 3: 改同步结果文案**

`KnowledgeView.vue:281` 把:
```typescript
    repoSourceMessage.value = `已导入 ${result.imported} 个文件，跳过 ${result.skipped} 个`
```
改为:
```typescript
    repoSourceMessage.value = `已同步：新增 ${result.added}，删除 ${result.removed}，更新 ${result.updated}`
```

- [ ] **Step 4: 模板操作列加删除按钮**

`KnowledgeView.vue:806-810` 把操作列改为(加红色删除按钮):

```vue
                <td class="px-3 py-2 text-right">
                  <div class="flex justify-end gap-2">
                    <Button variant="outline" size="sm" class="h-7 text-xs" @click="syncRepoSource(source)" :disabled="repoSourceSyncing[source.repo_key]">
                      {{ repoSourceSyncing[source.repo_key] ? '同步中...' : '立即同步' }}
                    </Button>
                    <Button variant="outline" size="sm" class="h-7 text-xs text-red-600 hover:text-red-700" @click="deleteRepoSource(source)" :disabled="repoSourceDeleting[source.repo_key]">
                      {{ repoSourceDeleting[source.repo_key] ? '删除中...' : '删除' }}
                    </Button>
                  </div>
                </td>
```

- [ ] **Step 5: 更新前端回归测试断言**

`tests/test_capability_api.py` 的 `test_frontend_knowledge_view_exposes_git_repo_source_controls`(约第 641 行)里加断言:

```python
    assert "deleteRepoSource" in source
    assert "deleteKbRepoSource" in client
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest tests/test_capability_api.py::test_frontend_knowledge_view_exposes_git_repo_source_controls -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add frontend/capabilities/src/views/knowledge/KnowledgeView.vue tests/test_capability_api.py
git commit -m "feat(ui): delete button with confirmation for git data sources"
```

---

### Task 13: 全量回归 + 收尾

- [ ] **Step 1: 跑全部后端测试**

Run: `python -m pytest tests/ -v`
Expected: 全绿。重点关注 git 源相关、storage migration、capability_api 测试。

- [ ] **Step 2: 前端类型检查 + 构建**

Run: `cd frontend/capabilities && npm run typecheck && npm run build`
Expected: 无错误。

- [ ] **Step 3: 手动验证(可选,若有本地运行环境)**

1. 启动应用,建 KB,登记 git 仓库,加数据源,同步 → 看到"新增 N"
2. 改仓库文件内容,再同步 → 看到"更新 1",doc_id 变化
3. 删仓库文件,再同步 → 看到"删除 1"
4. 点删除按钮 → 二次确认文案带文档数 → 确认后数据源消失 + 文档清空 + 生成 delete 任务

- [ ] **Step 4: 最终提交(若有未提交收尾)**

```bash
git add -A
git commit -m "test: full regression for git source sync and delete"
```
