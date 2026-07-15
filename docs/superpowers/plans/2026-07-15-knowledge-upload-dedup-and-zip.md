# Knowledge Upload Dedup and ZIP Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make same-content uploads idempotent within each knowledge base and import supported documents from ZIP uploads safely, while preserving filename-based slug renaming for different content.

**Architecture:** Keep `AgentBridgeService.add_document` as the public upload entry point. Split its normal-file path into a single-document helper, add a ZIP expansion helper under `docs_knowledge`, and perform SHA-256 duplicate lookup against the current document version scoped to each target knowledge base. Keep the regular upload response compatible and return an aggregate response for ZIP uploads.

**Tech Stack:** Python 3.11, FastAPI, SQLite, `zipfile`, Vue 3, TypeScript, native `node:test`.

---

### Task 1: Add current-version content lookup and reusable hashing

**Files:**
- Modify: `src/agent_bridge/knowledge_management/docs_knowledge/archive.py`
- Modify: `src/agent_bridge/storage/repositories/knowledge.py`
- Modify: `tests/test_storage.py`

- [x] **Step 1: Write the failing repository/hash tests**

Add tests proving that the archive exposes a SHA-256 calculation without copying and that the repository only matches an active document's current version in the requested KB:

```python
def test_archive_content_hash_matches_store_hash(tmp_path: Path) -> None:
    source = tmp_path / "guide.md"
    source.write_bytes(b"same content")
    archive = ArchiveStorage(tmp_path / "archive")
    assert archive.content_hash(source) == archive.store(source).content_hash


def test_find_current_document_by_content_hash_is_scoped_to_kb(wm_paths: AgentBridgePaths) -> None:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    kb_a = store.create_kb("kb-a", "KB A", "", "root")
    kb_b = store.create_kb("kb-b", "KB B", "", "root")
    doc = store.create_document("guide", "Guide", "root")
    store.attach_document_to_kb(doc["id"], kb_a["id"], "root")
    version = store.create_document_version(
        doc["id"], "guide.md", "hash-a", 4, "text/markdown", "/a", "root"
    )
    assert store.find_current_document_by_content_hash(kb_a["id"], "hash-a")["id"] == doc["id"]
    assert store.find_current_document_by_content_hash(kb_b["id"], "hash-a") is None
```

- [x] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_storage.py::test_archive_content_hash_matches_store_hash tests/test_storage.py::test_find_current_document_by_content_hash_is_scoped_to_kb
```

Expected: FAIL because `ArchiveStorage.content_hash` and `find_current_document_by_content_hash` do not exist.

- [x] **Step 3: Implement the smallest storage changes**

In `archive.py`, add a public method and make `store()` call it:

```python
def content_hash(self, source: Path) -> str:
    return self._sha256(source)

def store(self, source: Path) -> ArchivedFile:
    content_hash = self.content_hash(source)
    # keep the existing suffix, target layout, copy-if-missing, and return value
```

In `KnowledgeRepository`, add:

```python
def find_current_document_by_content_hash(self, kb_id: int, content_hash: str) -> dict[str, Any] | None:
    with self._connect() as conn:
        row = conn.execute(
            """
            SELECT d.*, v.version_no AS current_version_no,
                   v.original_filename AS current_original_filename,
                   v.content_hash AS current_content_hash,
                   v.id AS current_version_id
            FROM documents d
            JOIN document_kbs dk ON dk.doc_id = d.id
            JOIN document_versions v ON v.id = d.current_version_id
            WHERE dk.kb_id = ?
              AND dk.status = 'active'
              AND d.status = 'active'
              AND v.content_hash = ?
            ORDER BY d.id
            LIMIT 1
            """,
            (kb_id, content_hash),
        ).fetchone()
        return row_to_dict(row)
```

- [x] **Step 4: Run the focused tests and verify they pass**

Run the same command from Step 2. Expected: 2 passed.

- [x] **Step 5: Commit the storage unit**

```bash
git add src/agent_bridge/knowledge_management/docs_knowledge/archive.py src/agent_bridge/storage/repositories/knowledge.py tests/test_storage.py
git commit -m "feat: add knowledge document content lookup"
```

### Task 2: Implement safe ZIP expansion and service-level deduplication

**Files:**
- Create: `src/agent_bridge/knowledge_management/docs_knowledge/uploads.py`
- Modify: `src/agent_bridge/app/service.py`
- Modify: `tests/test_services.py`

- [x] **Step 1: Write failing service tests**

Add tests for exact-content skip, same-name/different-content slug allocation, KB scoping, ZIP recursion/filtering, ZIP-internal deduplication, and invalid/unsafe ZIP rejection:

```python
def test_duplicate_content_in_same_kb_is_skipped(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    first_source = tmp_path / "guide.md"
    second_source = tmp_path / "copy.md"
    first_source.write_bytes(b"same")
    second_source.write_bytes(b"same")
    service.add_document("root", first_source, ["kb"], later=True)
    second = service.add_document("root", second_source, ["kb"], later=True)
    assert second["skipped"] is True
    assert second["skip_reason"] == "duplicate_content"
    assert len(service.list_docs("root", "kb")) == 1


def test_same_filename_with_different_content_keeps_unique_slug(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    first_source = tmp_path / "guide.md"
    second_source = tmp_path / "other.md"
    first_source.write_bytes(b"one")
    second_source.write_bytes(b"two")
    first = service.add_document("root", first_source, ["kb"], later=True)
    second = service.add_document(
        "root", second_source, ["kb"], later=True,
        original_filename="guide.md",
    )
    assert [first["slug"], second["slug"]] == ["guide", "guide-2"]


def test_zip_imports_supported_nested_documents_and_skips_duplicate_content(wm_paths, tmp_path: Path) -> None:
    service = _service_with_mock_backend(wm_paths, tmp_path)
    service.create_kb("root", "kb", "KB", "")
    archive = tmp_path / "docs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("root.md", b"one")
        zf.writestr("nested/guide.pdf", b"two")
        zf.writestr("nested/copy.txt", b"one")
        zf.writestr("image.png", b"ignored")
        zf.writestr("nested/inner.zip", b"ignored")
    result = service.add_document("root", archive, ["kb"], later=True)
    assert result["uploaded_count"] == 2
    assert result["skipped_count"] == 1
    assert {doc["slug"] for doc in service.list_docs("root", "kb")} == {"root", "guide"}
```

Also add tests that a path such as `../escape.md`, a malformed ZIP, and a ZIP with no supported documents raise `ValidationError` and leave the KB document list empty.

- [x] **Step 2: Run the service tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_services.py -k 'duplicate_content or same_filename or zip_import or zip_rejects'
```

Expected: FAIL because ZIP is currently rejected and duplicate content is currently inserted as a new document.

- [x] **Step 3: Implement the ZIP helper**

Create `extract_zip_documents(source: Path, destination: Path, allowed_extensions: set[str]) -> list[Path]` that:

1. Opens the archive with `ZipFile` and converts `BadZipFile`, CRC failures, and read failures to `ValidationError`.
2. Rejects absolute member paths and any `PurePosixPath(info.filename).parts` containing `..`.
3. Ignores directories, symlink entries, `.zip` members, and unsupported suffixes.
4. Calls `testzip()` before writing any extracted document.
5. Writes supported members below `destination` using their validated relative path and returns them sorted by member name.
6. Raises `ValidationError("zip archive contains no supported documents")` when nothing is eligible.

- [x] **Step 4: Refactor the service into ZIP dispatch plus single-document ingestion**

In `AgentBridgeService.add_document`:

```python
display_name = original_filename or source.name
kbs = [self._require_kb_admin_visible(actor, kb_slug) for kb_slug in kb_slugs]
self._validate_source(source, allow_zip=True)
if source.suffix.lower() == ".zip":
    return self._add_zip_documents(actor, source, display_name, kbs, later)
return self._add_single_document(actor, source, display_name, kbs, later, source_type, source_repo_key, slug_override)
```

The single-document helper must:

- compute `content_hash = self.archive.content_hash(source)` before copying;
- find an existing current document by hash for each target KB;
- when a match exists, reuse that document, attach it to any missing target KB, queue `Operation.create` only for those newly attached targets, set `skipped=True` and `skip_reason="duplicate_content"`, and return without creating a version;
- when no match exists, preserve the existing slug, archive, version, attachment, and create-job behavior unchanged.

The ZIP helper must create a `TemporaryDirectory`, call `extract_zip_documents`, process the extracted files in sorted order, partition results by `skipped`, and return:

```python
{
    "source_filename": display_name,
    "source_type": "zip",
    "documents": uploaded_results,
    "skipped": skipped_results,
    "uploaded_count": len(uploaded_results),
    "skipped_count": len(skipped_results),
}
```

- [x] **Step 5: Run the focused service tests and verify they pass**

Run the command from Step 2. Expected: all new service tests pass.

- [x] **Step 6: Run existing knowledge service/storage tests**

```bash
uv run pytest -q tests/test_services.py tests/test_storage.py
```

Expected: all tests pass.

- [x] **Step 7: Commit the backend implementation**

```bash
git add src/agent_bridge/knowledge_management/docs_knowledge/uploads.py src/agent_bridge/app/service.py src/agent_bridge/storage/repositories/knowledge.py src/agent_bridge/knowledge_management/docs_knowledge/archive.py tests/test_services.py tests/test_storage.py
git commit -m "feat: deduplicate knowledge uploads and import zip documents"
```

### Task 3: Expose ZIP uploads and result feedback in the Vue client

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/views/knowledge/KnowledgeView.vue`
- Modify: `frontend/capabilities/tests/uploadDialogLayout.test.ts`

- [x] **Step 1: Write failing frontend contract tests**

Extend the source-level tests to require `.zip` in the allowed extensions and upload input accepts, plus an error/result display hook in the upload dialog.

- [x] **Step 2: Run the focused frontend test and verify it fails**

```bash
cd frontend/capabilities
node --import tsx --test tests/uploadDialogLayout.test.ts
```

Expected: FAIL because the current UI does not list ZIP or expose upload results/errors.

- [x] **Step 3: Add upload response types and ZIP UI support**

Define a `DocumentUploadSummary` with `source_filename`, `source_type`, `documents`, `skipped`, `uploaded_count`, and `skipped_count`, then type `api.addDocument` as `Promise<DocumentDetail | DocumentUploadSummary>`.

Update `KnowledgeView.vue` to:

- add `.zip` to `ALLOWED_DOC_EXTENSIONS`, both `accept` attributes, and the helper text;
- reset and display `uploadError` in the dialog;
- collect each upload response, count `uploaded_count`/`skipped_count` for ZIP responses and one successful document for ordinary responses;
- show a completion alert when duplicates were skipped;
- retain the existing dialog behavior for ordinary uploads.

- [x] **Step 4: Run focused frontend tests and typecheck**

```bash
node --import tsx --test tests/uploadDialogLayout.test.ts
npm run typecheck
```

Expected: focused tests pass and `vue-tsc` exits 0.

- [x] **Step 5: Run production build**

```bash
npm run build
```

Expected: Vite build exits 0.

- [x] **Step 6: Commit the frontend implementation**

```bash
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/knowledge/KnowledgeView.vue frontend/capabilities/tests/uploadDialogLayout.test.ts
git commit -m "feat(ui): support zip knowledge uploads and duplicate feedback"
```

### Task 4: Final verification and handoff

**Files:**
- No source changes; verify the commits and preserve unrelated worktree files.

- [x] **Step 1: Run backend regression tests**

```bash
uv run pytest -q tests/test_services.py tests/test_storage.py
```

- [x] **Step 2: Run frontend regression tests, typecheck, and build**

```bash
cd frontend/capabilities
node --import tsx --test tests/uploadDialogLayout.test.ts
npm run typecheck
npm run build
```

- [x] **Step 3: Inspect the final diff and status**

```bash
git diff --check
git show --check --oneline HEAD
git status --short
git log --oneline -4
```

Confirm that only the design/plan and feature files are committed, while unrelated pre-existing worktree files remain uncommitted.
