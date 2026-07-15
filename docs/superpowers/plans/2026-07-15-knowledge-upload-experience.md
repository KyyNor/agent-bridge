# Knowledge Upload Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add upload progress, recursive and encoding-tolerant ZIP ingestion, atomic ZIP failures with Chinese diagnostics, ZIP-aware browsing, fixed-height directory scrolling, faster hashing/uploads, and immediate sync-task refreshes.

**Architecture:** Keep POST /docs compatible. Add a transaction-aware SQLite context and a recursive ZIP extraction result carrying normalized virtual-tree entries and one-pass hashes. Persist ZIP metadata in knowledge_archive_entries, expose it through a KB-scoped browse endpoint, and keep ZIP containers out of backend synchronization. The Vue dialog uses XHR progress and limited concurrency; all successful mutations call one shared refresh routine.

**Tech Stack:** Python 3.11, FastAPI, SQLite, zipfile, SHA-256, Vue 3 script setup, TypeScript, native Node test runner, pytest.

---

## File map

- Modify src/agent_bridge/storage/schema.py and sqlite.py for archive schema, migration, transactions, and SQLiteStore facade wrappers.
- Modify src/agent_bridge/storage/repositories/knowledge.py for archive entries, archive-aware placements, and browse queries.
- Modify src/agent_bridge/knowledge_management/docs_knowledge/archive.py and uploads.py for one-pass hashing, recursive ZIP safety, and filename decoding.
- Modify src/agent_bridge/app/service.py for atomic ZIP ingestion, browse responses, and relative sync paths.
- Modify src/agent_bridge/api/routes/knowledge.py and api/schemas.py for the browse endpoint.
- Modify frontend/capabilities/src/api/types.ts, api/client.ts, and views/knowledge/KnowledgeView.vue for types, XHR progress, modal state, browse navigation, and refreshes.
- Modify frontend/capabilities/tests/uploadDialogLayout.test.ts.
- Modify tests/test_uploads.py, test_storage.py, test_services.py, test_folder_sync.py, test_knowledge_folder_api.py, and test_server.py.
- Create tests/test_knowledge_archive_api.py.
- Modify tests/test_e2e.py for a complete upload-to-browse-to-status flow.

### Task 1: Add transactional archive storage

**Files:**
- Modify: src/agent_bridge/storage/schema.py
- Modify: src/agent_bridge/storage/sqlite.py
- Modify: src/agent_bridge/storage/repositories/knowledge.py
- Test: tests/test_storage.py
- Test: tests/test_knowledge_folders.py

- [ ] **Step 1: Write the failing schema and rollback tests.**

~~~python
def test_archive_schema_and_document_placement_column_are_created(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    with store.connect() as conn:
        archive_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(knowledge_archive_entries)"
        )}
        placement_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(document_kbs)"
        )}
    assert {"id", "kb_id", "parent_id", "parent_folder_id", "kind",
            "name", "relative_path", "doc_id"} <= archive_columns
    assert "archive_entry_id" in placement_columns


def test_store_transaction_rolls_back_archive_and_document_rows(wm_paths):
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb = store.create_kb("kb", "KB", "", "root")
    root_id = store.list_folder_tree(kb["id"])[0]["id"]
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.create_document("guide", "Guide", "root")
            store.create_archive_entry(
                kb["id"], kind="zip", name="docs.zip",
                relative_path="docs.zip", parent_folder_id=root_id,
            )
            raise RuntimeError("rollback")
    assert store.get_document_by_slug("guide") is None
    assert store.list_archive_entries(kb["id"]) == []
~~~

- [ ] **Step 2: Run the focused tests and verify the expected failure.**

~~~bash
uv run pytest -q tests/test_storage.py -k 'archive_schema or transaction_rolls_back'
~~~

Expected: FAIL because the archive table, placement column, transaction context, and repository methods do not exist.

- [ ] **Step 3: Implement the schema migration and transaction-aware connection.**

Add knowledge_archive_entries to SCHEMA with id, kb_id, parent_id, parent_folder_id, kind, name, relative_path, doc_id, status, created_at, and updated_at. Add foreign keys and indexes for (kb_id, parent_id, status), (kb_id, parent_folder_id, status), and document_kbs.archive_entry_id.

In SQLiteStore, add a ContextVar holding the active sqlite3.Connection. transaction() opens one outer connection and commits/rolls back it. Nested connect() calls yield the active connection without committing. Existing callers outside a transaction keep their current behavior.

Add these repository methods:

~~~python
def create_archive_entry(
    self, kb_id: int, *, kind: str, name: str, relative_path: str,
    parent_id: int | None = None, parent_folder_id: int | None = None,
    doc_id: int | None = None,
) -> dict[str, Any]: ...

def list_archive_entries(
    self, kb_id: int, *, parent_id: int | None = None,
    parent_folder_id: int | None = None, active_only: bool = True,
) -> list[dict[str, Any]]: ...

def get_archive_entry(self, kb_id: int, entry_id: int) -> dict[str, Any] | None: ...
def update_archive_entry_document(self, entry_id: int, doc_id: int) -> None: ...
def delete_archive_entries_for_kb(self, kb_id: int) -> None: ...
~~~

Validate exactly one parent selector and kind in {"zip", "folder", "document"}. Sort direct children by name COLLATE NOCASE, then id.
Add matching SQLiteStore facade methods, and extend attach_document_to_kb and update_document_placement with an optional archive_entry_id so service code does not reach into the repository object directly.

- [ ] **Step 4: Run storage and folder regressions.**

~~~bash
uv run pytest -q tests/test_storage.py tests/test_knowledge_folders.py
git diff --check
~~~

Expected: existing tests and the new storage tests pass.

- [ ] **Step 5: Commit the storage boundary.**

~~~bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py src/agent_bridge/storage/repositories/knowledge.py tests/test_storage.py tests/test_knowledge_folders.py
git commit -m "feat: add transactional knowledge archive storage"
~~~

### Task 2: Make ZIP extraction recursive, safe, and encoding-tolerant

**Files:**
- Modify: src/agent_bridge/knowledge_management/docs_knowledge/archive.py
- Modify: src/agent_bridge/knowledge_management/docs_knowledge/uploads.py
- Test: tests/test_uploads.py

- [ ] **Step 1: Write failing recursive, filename, error-chain, and hash tests.**

~~~python
def test_extract_zip_documents_recurses_and_preserves_archive_tree(tmp_path: Path):
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("手册/api.md", b"api")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("release/guide.txt", b"guide")
        zf.writestr("release/manuals.zip", inner.read_bytes())

    result = extract_zip_documents(outer, tmp_path / "out", {".md", ".txt"})

    assert "release/guide.txt" in [item.relative_path for item in result.documents]
    assert any(item.kind == "zip" for item in result.entries)
    assert any(item.relative_path.endswith("手册/api.md")
               for item in result.documents)


def test_extract_zip_documents_reports_inner_zip_chain(tmp_path: Path):
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("broken.zip", b"not a zip")
    with pytest.raises(ValidationError, match="broken.zip|内层 ZIP"):
        extract_zip_documents(outer, tmp_path / "out", {".md"})
~~~

Add a legacy GB18030 central-directory filename fixture without the UTF-8 flag and assert its returned path contains the readable Chinese name. Add an assertion that each extracted document hash equals hashlib.sha256(path.read_bytes()).hexdigest().

- [ ] **Step 2: Run the focused tests and verify the old implementation fails.**

~~~bash
uv run pytest -q tests/test_uploads.py -k 'nested or encoding or chain or content_hash'
~~~

Expected: FAIL because nested ZIP members are rejected and the result has no archive-tree metadata or hash fields.

- [ ] **Step 3: Implement the recursive extraction result and decoder.**

Define:

~~~python
@dataclass(frozen=True)
class ExtractedDocument:
    path: Path
    relative_path: str
    content_hash: str
    file_size: int

@dataclass(frozen=True)
class ExtractedArchiveEntry:
    kind: Literal["zip", "folder", "document"]
    relative_path: str
    name: str
    parent_path: str | None
    content_hash: str | None = None
    file_size: int | None = None

@dataclass(frozen=True)
class ExtractedZip:
    entries: list[ExtractedArchiveEntry]
    documents: list[ExtractedDocument]
~~~

For non-UTF-8 names, try utf-8, gb18030, big5, shift_jis, and cp437, scoring printable text and CJK/Japanese characters positively and replacement/control/CP437 box-drawing characters negatively. UTF-8 flag decoding remains authoritative. ASCII names remain unchanged.

At every archive level, normalize separators, reject absolute paths, dot-dot segments, symlinks, encrypted members, duplicate normalized paths, CRC/read failures, and recursion deeper than 8. Call testzip() before writing. Recurse through ZIP members and prefix child paths with the nested ZIP member path. Build virtual folder entries for parent paths. Hash supported files while copying them to the temporary directory. Wrap every ValidationError with the complete archive chain. Reject an archive level without supported descendants with a Chinese message.

- [ ] **Step 4: Reuse the precomputed hash in ArchiveStorage.store.**

Change the method to accept optional content_hash and file_size:

~~~python
def store(
    self, source: Path, *, content_hash: str | None = None,
    file_size: int | None = None,
) -> ArchivedFile:
~~~

Compute missing values as before; use supplied values for the content-addressed target and returned metadata. Keep suffix handling and copy-if-missing behavior unchanged.

- [ ] **Step 5: Run extraction and extension regressions, then commit.**

~~~bash
uv run pytest -q tests/test_uploads.py tests/test_allowed_extensions.py
git add src/agent_bridge/knowledge_management/docs_knowledge/archive.py src/agent_bridge/knowledge_management/docs_knowledge/uploads.py tests/test_uploads.py
git commit -m "feat: recursively extract encoded knowledge zips"
~~~

Expected: recursive extraction, Chinese filenames, path/symlink safety, CRC failures, one-pass metadata, and existing extension tests pass.

### Task 3: Add atomic ZIP ingestion and correct folder-capable sync paths

**Files:**
- Modify: src/agent_bridge/app/service.py
- Modify: src/agent_bridge/storage/repositories/knowledge.py
- Test: tests/test_services.py
- Test: tests/test_folder_sync.py

- [ ] **Step 1: Write failing service tests.**

Cover nested import and archive entries, corrupt-inner rollback, ZIP duplicate content, and folder-capable sync path. The rollback assertion must prove list_docs, list_archive_entries, and status.jobs are all empty after a corrupted inner ZIP.

~~~python
def test_corrupt_inner_zip_rolls_back_all_rows(wm_paths, tmp_path: Path):
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    outer = tmp_path / "release.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("good.md", b"good")
        zf.writestr("broken.zip", b"not a zip")

    with pytest.raises(ValidationError, match="broken.zip"):
        service.add_document("root", outer, ["kb"], later=True)

    assert service.list_docs("root", "kb") == []
    assert service.list_archive_entries("root", "kb") == []
    assert service.status("root")["jobs"] == []
~~~

- [ ] **Step 2: Run the focused tests and verify the expected failures.**

~~~bash
uv run pytest -q tests/test_services.py tests/test_folder_sync.py -k 'zip or archive or relative_path'
~~~

Expected: FAIL because nested ZIPs are rejected, archive-entry service methods do not exist, and _run_job strips relative filenames before building folder-capable remote paths.

- [ ] **Step 3: Refactor single-document ingestion for reusable metadata and archive placement.**

Add optional content_hash, file_size, and archive_entry_id parameters to _add_single_document. Normal files keep existing relative-path folder creation and pass the one computed hash to ArchiveStorage.store. ZIP children attach to the selected real folder, store the internal relative path in document_versions.original_filename, and set archive_entry_id on the new placement. Deduplication still matches current content inside the target KB; duplicates create a virtual file entry but do not move the existing placement or create another version.
Expose service.list_archive_entries(actor, kb_slug) for the service tests and for archive cleanup assertions; it must enforce the same admin-visible KB check as list_folders.

- [ ] **Step 4: Implement ZIP preflight plus one SQLite transaction.**

Call the recursive extractor fully inside TemporaryDirectory before database writes. Then enter with self.store.transaction(): create the outer ZIP entry under the selected real folder; create virtual folders, nested ZIPs, and document entries parent-before-child; ingest children with precomputed hashes; link file entries to document IDs; attach new placements; queue create jobs. Track archive files newly written by this batch and remove only unreferenced new files if the transaction rolls back. Keep the existing successful summary fields source_filename, source_type, documents, skipped, uploaded_count, and skipped_count.

- [ ] **Step 5: Build the folder-capable remote path from the full relative filename.**

In _run_job, normalize the version filename once. For supports_folders=True, use join_backend_path(folder_path, normalized_original_filename). For flat backends, continue sending Path(original_filename).name.

- [ ] **Step 6: Run the focused backend tests and commit.**

~~~bash
uv run pytest -q tests/test_services.py tests/test_folder_sync.py tests/test_uploads.py
git add src/agent_bridge/app/service.py src/agent_bridge/storage/repositories/knowledge.py tests/test_services.py tests/test_folder_sync.py
git commit -m "feat: atomically ingest and browse knowledge archives"
~~~

Expected: nested import, rollback, deduplication, archive entries, and backend path tests pass.

### Task 4: Expose archive browsing and friendly upload errors through the API

**Files:**
- Modify: src/agent_bridge/api/schemas.py
- Modify: src/agent_bridge/api/routes/knowledge.py
- Modify: src/agent_bridge/app/service.py
- Create: tests/test_knowledge_archive_api.py
- Modify: tests/test_knowledge_folder_api.py
- Modify: tests/test_server.py

- [ ] **Step 1: Write failing browse and error-response tests.**

Create a ZIP with manuals/api.md, POST it to /docs, GET /kbs/docs/browse, assert the root contains a kind=zip entry, then GET browse with archive_entry_id and assert the internal folder is returned. Add a malformed-inner-ZIP request and assert HTTP 400, Chinese detail containing broken.zip, and an empty docs response.

- [ ] **Step 2: Run the API tests and verify the route is absent.**

~~~bash
uv run pytest -q tests/test_knowledge_archive_api.py tests/test_knowledge_folder_api.py tests/test_server.py -k 'browse or zip or upload'
~~~

Expected: FAIL with a missing browse route or missing ZIP-aware entries.

- [ ] **Step 3: Add the route and service contract.**

Add:

~~~python
@router.get("/kbs/{kb_slug}/browse")
def browse_kb(
    kb_slug: str,
    folder_id: int | None = None,
    archive_entry_id: int | None = None,
    current_actor: str = Depends(actor),
) -> dict[str, Any]:
    return service.browse_kb(
        current_actor, kb_slug,
        folder_id=folder_id,
        archive_entry_id=archive_entry_id,
    )
~~~

Reject both IDs together. For a real folder, return direct child real folders, outer ZIP entries, and document placements where archive_entry_id IS NULL. For an archive entry, return direct archive children and reject entries from another KB. Document entries include slug, title, original filename, version, and sync status. Keep /docs unchanged.

- [ ] **Step 4: Run API regressions and commit.**

~~~bash
uv run pytest -q tests/test_knowledge_archive_api.py tests/test_knowledge_folder_api.py tests/test_server.py
git add src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/knowledge.py src/agent_bridge/app/service.py tests/test_knowledge_archive_api.py tests/test_knowledge_folder_api.py tests/test_server.py
git commit -m "feat: expose knowledge archive browsing"
~~~

Expected: browse entries, nested error detail, folder CRUD, temporary-upload cleanup, and existing document API behavior pass.

### Task 5: Add XHR progress, limited concurrency, and modal error rendering

**Files:**
- Modify: frontend/capabilities/src/api/types.ts
- Modify: frontend/capabilities/src/api/client.ts
- Modify: frontend/capabilities/src/views/knowledge/KnowledgeView.vue
- Modify: frontend/capabilities/tests/uploadDialogLayout.test.ts

- [ ] **Step 1: Write failing frontend source-contract tests.**

~~~ts
test("upload dialog renders progress stages and keeps failure details visible", () => {
  const file = readKnowledgeView()
  const dialog = file.slice(file.indexOf("<!-- 上传文档对话框 -->"))
  assert.match(dialog, /progress|进度/)
  assert.match(dialog, /processing|处理中/)
  assert.match(dialog, /uploadError|error/)
  assert.match(dialog, /show-close-button|showCloseButton/)
})

test("upload client exposes XHR progress and detail parsing", () => {
  const client = readApiClient()
  assert.match(client, /XMLHttpRequest/)
  assert.match(client, /upload\\.onprogress/)
  assert.match(client, /detail/)
})
~~~

- [ ] **Step 2: Run the focused frontend test and verify it fails.**

~~~bash
cd frontend/capabilities
node --test tests/uploadDialogLayout.test.ts
~~~

Expected: FAIL because the client uses fetch and the dialog lacks item state and progress rendering.

- [ ] **Step 3: Implement typed XHR FormData upload and readable errors.**

Add:

~~~ts
export type UploadProgressCallback = (loaded: number, total: number) => void

function postFormDataWithProgress<T>(
  url: string,
  formData: FormData,
  onProgress?: UploadProgressCallback,
): Promise<T> {
  // XMLHttpRequest, X-Agent-Bridge-User, upload.onprogress,
  // JSON response parsing, and response.detail on non-2xx.
}
~~~

Keep fetch-based postFormData for other callers. Extend addDocument with an optional final callback and use the XHR helper. Parse JSON detail first, then plain text, then return 上传失败（HTTP 状态码）.

- [ ] **Step 4: Implement the modal state machine and three-worker queue.**

Extend UploadItem with status, progress, stage, and error. Use a shared queue index and three async workers. Each worker sets uploading, passes the XHR callback to api.addDocument, sets processing after the network completes, then marks succeeded or failed. A failed ZIP keeps its full nested error chain visible and does not close the dialog. Use show-close-button="!uploading" and disable the footer close action while workers are active.

- [ ] **Step 5: Run the focused test and commit.**

~~~bash
node --test tests/uploadDialogLayout.test.ts
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/knowledge/KnowledgeView.vue frontend/capabilities/tests/uploadDialogLayout.test.ts
git commit -m "feat(ui): show knowledge upload progress and errors"
~~~

Expected: all upload contract tests pass.

### Task 6: Render archive-aware browsing, constrain the directory, and refresh sync tasks

**Files:**
- Modify: frontend/capabilities/src/api/types.ts
- Modify: frontend/capabilities/src/api/client.ts
- Modify: frontend/capabilities/src/views/knowledge/KnowledgeView.vue
- Modify: frontend/capabilities/tests/uploadDialogLayout.test.ts

- [ ] **Step 1: Write failing browser-layout assertions.**

~~~ts
test("knowledge browser renders folder and zip entries and constrains scrolling", () => {
  const file = readKnowledgeView()
  assert.match(file, /browse/)
  assert.match(file, /kind.*zip|entry\\.kind.*zip/)
  assert.match(file, /overflow-y-auto/)
  assert.match(file, /h-\\[calc\\(100vh-/)
})
~~~

- [ ] **Step 2: Run the test and verify it fails.**

~~~bash
cd frontend/capabilities
node --test tests/uploadDialogLayout.test.ts
~~~

Expected: FAIL because the right pane renders only detailDocs and the folder tree has no fixed-height scroll wrapper.

- [ ] **Step 3: Add browse types and client method.**

Define discriminated KnowledgeBrowseEntry types for folder, zip, and document plus KnowledgeBrowseContext and KnowledgeBrowseResponse. Add:

~~~ts
listBrowse: (kbSlug: string, folderId?: number, archiveEntryId?: number) => {
  const qs = new URLSearchParams()
  if (folderId != null) qs.set("folder_id", String(folderId))
  if (archiveEntryId != null) qs.set("archive_entry_id", String(archiveEntryId))
  return get<KnowledgeBrowseResponse>("/kbs/" + kbSlug + "/browse?" + qs)
}
~~~

- [ ] **Step 4: Implement right-pane navigation and icons.**

Keep the left real-folder FolderTree. Add browseArchiveEntryId, browseEntries, and browseContext refs. Selecting a real folder loads folder entries; clicking a ZIP or virtual folder loads by archive_entry_id; back loads the parent context. Render distinct Folder, Archive, and File icons. Preserve checkbox, move, attach, delete, and detail actions for document entries only. Keep “全部文档” compatibility mode backed by listDocs when it is not a browse context.

- [ ] **Step 5: Add fixed-height scrolling and one shared refresh routine.**

Wrap FolderTree in h-[calc(100vh-280px)] min-h-[320px] max-h-[720px] overflow-y-auto, retaining the existing 240–420px resizable width. Add refreshKnowledgeDetail() that refreshes summary, folders, browse/current docs, and getSyncStatus() before setting detailSyncJobs. Call it after upload success, Git sync/delete, folder CRUD, placement/attach, and scoped deletion. Update the sync tab count from the refreshed array.

- [ ] **Step 6: Run frontend verification and commit.**

~~~bash
node --test tests/uploadDialogLayout.test.ts
npm run typecheck
npm run build
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/knowledge/KnowledgeView.vue frontend/capabilities/tests/uploadDialogLayout.test.ts
git commit -m "feat(ui): browse zip containers and refresh sync tasks"
~~~

Expected: focused tests, vue-tsc, and Vite build pass; dist remains ignored.

### Task 7: Run grouped regression and final verification

**Files:**
- Modify: tests/test_e2e.py
- Modify: docs/superpowers/specs/2026-07-15-knowledge-upload-experience-design.html

- [ ] **Step 1: Add one complete API flow.**

Use TestClient to create a KB, upload a ZIP containing a nested ZIP and a normal document, browse the outer ZIP, enter nested contents, and call /status. Assert ZIP containers and extracted documents are visible and pending sync jobs exist.

- [ ] **Step 2: Run grouped backend regression.**

~~~bash
uv run pytest -q tests/test_uploads.py tests/test_services.py tests/test_storage.py tests/test_knowledge_folders.py tests/test_knowledge_folder_api.py tests/test_knowledge_archive_api.py tests/test_folder_sync.py tests/test_server.py tests/test_e2e.py
~~~

Expected: feature tests pass. Preserve exact output of unrelated pre-existing failures rather than masking them.

- [ ] **Step 3: Run frontend and repository hygiene checks.**

~~~bash
cd frontend/capabilities
node --test tests/*.test.ts
npm run typecheck
npm run build
cd /Users/kyynor/Code/agent-bridge
git diff --check
git status --short
~~~

Expected: Node tests, typecheck, build, and diff check pass; generated dist is ignored and unrelated files are unstaged.

- [ ] **Step 4: Update the design document with verified facts and commit final evidence.**

Only record facts confirmed by tests, then run:

~~~bash
git add docs/superpowers/specs/2026-07-15-knowledge-upload-experience-design.html tests/test_e2e.py
git commit -m "test: verify knowledge upload experience end to end"
~~~
