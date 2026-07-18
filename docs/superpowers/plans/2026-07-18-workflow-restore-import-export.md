# Workflow Restore and Import/Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add append-only workflow revision restore, source-aware revision history, and preview/confirm workflow definition import/export for new and existing workflows.

**Architecture:** Centralize revision creation in `WorkflowService.upsert_definition` with a keyword-only `revision_source` defaulting to `edit`; restore and confirmed imports use the same transactional path. Export uses a stable JSON envelope containing only the current workflow definition. Import preview stores a short-lived actor-bound session containing the normalized definition and the target revision observed during preview; confirmation revalidates it before applying an `import` revision.

**Tech Stack:** Python/FastAPI, SQLite, Pydantic, existing workflow validator and diff utilities, Vue 3/TypeScript, Node test runner. No new runtime dependencies.

---

## Task 1: Add revision source to persistence and service contracts

**Files:**
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/sqlite.py`
- Modify: `src/agent_bridge/storage/repositories/workflows.py`
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Test: `tests/test_versioning_workflows.py`

- [ ] **Step 1: Write the failing source tests**

```python
def test_workflow_revision_records_edit_source(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    assert service.workflows.get_revision("root", "wf", 1)["source"] == "edit"


def test_workflow_revision_source_can_be_selected(wm_paths):
    service = _make_service(wm_paths)
    service.workflows.upsert_definition(
        actor="root", workflow_key="wf", name="W", description="",
        profile_key="p1", status="active", workflow_type="operation",
        definition={"nodes": [dict(GET_TASK_NODE)], "edges": []},
        revision_source="import",
    )
    assert service.workflows.get_revision("root", "wf", 1)["source"] == "import"
```

- [ ] **Step 2: Verify RED**

Run `pytest -q tests/test_versioning_workflows.py -k source`. It must fail because the schema, repository payload, and service signature do not expose `source`.

- [ ] **Step 3: Implement the minimal persistence change**

Add `source TEXT NOT NULL DEFAULT 'edit'` to `workflow_definition_revisions` in `WORKFLOW_SCHEMA`, and add this idempotent migration in `SQLiteStore.init_schema()`:

```python
self._ensure_columns(
    conn,
    "workflow_definition_revisions",
    {"source": "TEXT NOT NULL DEFAULT 'edit'"},
)
```

Change `create_definition_revision(workflow_key: str, content_hash: str, snapshot: dict[str, Any], actor: str, source: str = "edit")` to insert the value and include it in revision list/get responses. Add `revision_source: str = "edit"` to `WorkflowService.upsert_definition`, accept only `edit`, `import`, or `restore`, and pass it to the archive method. Keep the existing content-hash deduplication.

- [ ] **Step 4: Verify GREEN**

Run `pytest -q tests/test_versioning_workflows.py tests/test_diff_and_syntax.py`; all focused tests must pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/sqlite.py \
  src/agent_bridge/storage/repositories/workflows.py \
  src/agent_bridge/automation/workflows/service.py tests/test_versioning_workflows.py
git commit -m "feat: record workflow revision sources"
```

## Task 2: Implement restore and export APIs

**Files:**
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Modify: `src/agent_bridge/api/routes/workflows.py`
- Modify: `tests/test_versioning_workflows.py`
- Create: `tests/test_workflow_versioning_api.py`

- [ ] **Step 1: Write failing restore/export tests**

```python
def test_restore_revision_appends_new_restore_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, name="v1")
    _upsert(service, name="v2")
    restored = service.workflows.restore_revision("root", "wf", 1)
    assert restored["revision_no"] == 3
    assert restored["name"] == "v1"
    assert service.workflows.get_revision("root", "wf", 3)["source"] == "restore"


def test_export_contains_current_definition_but_not_execution_data(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service)
    exported = service.workflows.export_definition("root", "wf")
    assert exported["format"] == "agent-bridge.workflow"
    assert exported["format_version"] == 1
    assert exported["workflow"]["workflow_key"] == "wf"
    assert exported["revision"]["source"] == "edit"
    assert "runs" not in exported and "artifacts" not in exported
```

- [ ] **Step 2: Verify RED**

Run `pytest -q tests/test_versioning_workflows.py -k 'restore or export'`. It must fail because the service methods and routes do not exist.

- [ ] **Step 3: Implement service methods**

Implement `restore_revision(actor, workflow_key, revision_no)` by loading the revision snapshot, validating its workflow identity, and calling `upsert_definition` with the snapshot fields and `revision_source="restore"`. Return the saved public definition plus the source and restored-from revision. Implement `export_definition(actor, workflow_key)` returning:

```python
{
    "format": "agent-bridge.workflow",
    "format_version": 1,
    "exported_at": datetime.now(timezone.utc).isoformat(),
    "exported_by": actor,
    "workflow": {
        "workflow_key": workflow["workflow_key"], "name": workflow["name"],
        "description": workflow["description"], "profile_key": workflow["profile_key"],
        "status": workflow["status"], "workflow_type": workflow["workflow_type"],
        "definition": workflow["definition"],
    },
    "revision": {
        "revision_no": revision["revision_no"], "content_hash": revision["content_hash"],
        "source": revision["source"], "created_by": revision["created_by"],
        "created_at": revision["created_at"],
    },
}
```

- [ ] **Step 4: Add HTTP routes and tests**

Add `POST /workflows/{workflow_key}/revisions/{revision_no}/restore`. Add `GET /workflows/{workflow_key}/export` that serializes the envelope as UTF-8 JSON with media type `application/json` and Content-Disposition filename `<workflow_key>.workflow.json`. Use existing admin authorization and assert status, headers, revision number, and source in TestClient tests.

- [ ] **Step 5: Verify and commit**

Run `pytest -q tests/test_versioning_workflows.py tests/test_workflow_versioning_api.py`, then commit:

```bash
git add src/agent_bridge/automation/workflows/service.py src/agent_bridge/api/routes/workflows.py \
  tests/test_versioning_workflows.py tests/test_workflow_versioning_api.py
git commit -m "feat: add workflow restore and export APIs"
```

## Task 3: Implement import preview sessions and confirmation

**Files:**
- Modify: `src/agent_bridge/storage/schema.py`
- Modify: `src/agent_bridge/storage/repositories/workflows.py`
- Modify: `src/agent_bridge/automation/workflows/service.py`
- Modify: `src/agent_bridge/api/routes/workflows.py`
- Modify: `src/agent_bridge/api/schemas.py`
- Create: `tests/test_workflow_import.py`

- [ ] **Step 1: Write failing service tests**

```python
def test_import_preview_creates_new_workflow_session(wm_paths):
    service = _make_service(wm_paths)
    preview = service.workflows.preview_definition_import(
        actor="root", filename="source.workflow.json",
        content=json.dumps(_export_payload("new-wf")).encode(),
        target_workflow_key=None, target_mode="auto",
    )
    assert preview["operation"] == "create"
    assert preview["target_workflow_key"] == "new-wf"
    assert preview["can_confirm"] is True
    assert preview["diff"] is None


def test_import_preview_existing_returns_diff_and_confirm_appends_revision(wm_paths):
    service = _make_service(wm_paths)
    _upsert(service, name="old")
    preview = service.workflows.preview_definition_import(
        actor="root", filename="wf.workflow.json",
        content=json.dumps(_export_payload("wf", name="new")).encode(),
        target_workflow_key="wf", target_mode="overwrite",
    )
    assert preview["operation"] == "overwrite"
    assert preview["target_revision_no"] == 1
    assert preview["diff"]["structured"]["identical"] is False
    saved = service.workflows.confirm_definition_import("root", preview["import_id"])
    assert saved["revision_no"] == 2
    assert service.workflows.get_revision("root", "wf", 2)["source"] == "import"
```

Also cover: existing-key conflict in `new`/auto mode, invalid envelope, unsupported format version, expired session, actor mismatch, and stale target revision.

- [ ] **Step 2: Verify RED**

Run `pytest -q tests/test_workflow_import.py`; it must fail because import sessions and service methods do not exist.

- [ ] **Step 3: Add import-session storage**

Add `workflow_definition_imports` with `import_id`, actor, filename, target key, operation, normalized workflow JSON, observed target revision, source key, status, expiry, and timestamps. Add indexes on `(status, expires_at)` and `(actor, created_at)`. Implement create, actor-scoped get, mark-confirmed, delete-expired, and delete-for-workflow methods using the existing JSON helpers.

- [ ] **Step 4: Implement preview normalization**

Add `preview_definition_import` to parse UTF-8 JSON, require `format == "agent-bridge.workflow"` and `format_version == 1`, validate through `WorkflowValidator`, and normalize `target_workflow_key` and `target_mode` (`auto`, `new`, `overwrite`). A new target returns `operation="create"` and no diff. An existing target returns structured and unified diff against the current revision, stores the observed revision, and returns `import_id`, expiry, conflict/validation data, and `can_confirm`.

- [ ] **Step 5: Implement confirmation guards**

Add `confirm_definition_import(actor, import_id)` to load only an actor-owned, unexpired, previewed session, recheck target revision (`0` for a new key), validate again, and call `upsert_definition` with the session fields and `revision_source="import"`. Mark it single-use after success. Raise conflict on revision mismatch or key collision and never mutate the workflow on failure.

- [ ] **Step 6: Add multipart routes and HTTP tests**

Use `UploadFile` plus `Form` for preview and a JSON `WorkflowImportConfirmRequest` for confirmation:

```python
@router.post("/workflows/import/preview")
async def preview_workflow_import(
    file: UploadFile = File(description="workflow export JSON"),
    target_workflow_key: str | None = Form(None),
    target_mode: str = Form("auto"),
    current_actor: str = Depends(actor),
):
    return service.workflows.preview_definition_import(
        actor=current_actor,
        filename=file.filename or "workflow.workflow.json",
        content=await file.read(),
        target_workflow_key=target_workflow_key,
        target_mode=target_mode,
    )

@router.post("/workflows/import/confirm")
def confirm_workflow_import(
    payload: WorkflowImportConfirmRequest,
    current_actor: str = Depends(actor),
):
    return service.workflows.confirm_definition_import(current_actor, payload.import_id)
```

Test new-workflow creation, existing-workflow diff/overwrite, key conflict, stale preview, expiry, and invalid JSON through TestClient.

- [ ] **Step 7: Verify and commit**

Run `pytest -q tests/test_workflow_import.py tests/test_versioning_workflows.py tests/test_diff_and_syntax.py`, then commit:

```bash
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/repositories/workflows.py \
  src/agent_bridge/automation/workflows/service.py src/agent_bridge/api/routes/workflows.py \
  src/agent_bridge/api/schemas.py tests/test_workflow_import.py
git commit -m "feat: add workflow import preview and confirmation"
```

## Task 4: Wire revision sources and restore in the frontend

**Files:**
- Modify: `frontend/capabilities/src/api/types.ts`
- Modify: `frontend/capabilities/src/api/client.ts`
- Modify: `frontend/capabilities/src/components/version/RevisionHistoryPanel.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`
- Modify: `frontend/capabilities/tests/versioningLayout.test.ts`

- [ ] **Step 1: Write failing layout assertions**

```typescript
test('workflow history exposes source labels and restore action', () => {
  const panel = readSrc('src/components/version/RevisionHistoryPanel.vue')
  assert.match(panel, /sourceLabel|来源/)
  assert.match(panel, /restoreWorkflowRevision|恢复此版本/)
})

test('workflow API client exposes export, restore, and import calls', () => {
  const file = readSrc('src/api/client.ts')
  assert.match(file, /restoreWorkflowRevision/)
  assert.match(file, /exportWorkflow/)
  assert.match(file, /previewWorkflowImport/)
  assert.match(file, /confirmWorkflowImport/)
})
```

- [ ] **Step 2: Verify RED**

Run `cd frontend/capabilities && npx tsx --test tests/versioningLayout.test.ts`; it must fail before the new wiring exists.

- [ ] **Step 3: Implement types and client**

Extend `Revision` with `source?: 'edit' | 'import' | 'restore'`, add import preview/confirm types, and implement client calls for restore, blob export, multipart preview, and JSON confirmation.

- [ ] **Step 4: Implement history UI**

Render `编辑`, `导入`, and `回退` beside creator/time. Add a workflow-only restore action with confirmation, reload revisions after success, and invoke a parent refresh callback. Preserve script/skill behavior.

- [ ] **Step 5: Verify and commit**

Run `cd frontend/capabilities && npx tsx --test tests/versioningLayout.test.ts && npm run typecheck`, then commit the changed frontend files with `git commit -m "feat: expose workflow revision sources and restore"`.

## Task 5: Add workflow import dialog and export/import actions

**Files:**
- Create: `frontend/capabilities/src/components/workflow/WorkflowImportDialog.vue`
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`
- Modify: `frontend/capabilities/tests/versioningLayout.test.ts`

- [ ] **Step 1: Write failing UI assertions**

```typescript
test('workflow import UI supports new, overwrite, diff, and confirmation', () => {
  const dialog = readSrc('src/components/workflow/WorkflowImportDialog.vue')
  const view = readSrc('src/views/workflow/WorkflowView.vue')
  assert.match(dialog, /新工作流/)
  assert.match(dialog, /覆盖现有工作流/)
  assert.match(dialog, /WorkflowStructuredDiff/)
  assert.match(dialog, /确认导入/)
  assert.match(view, /导出工作流/)
  assert.match(view, /导入工作流/)
})
```

- [ ] **Step 2: Verify RED**

Run `cd frontend/capabilities && npx tsx --test tests/versioningLayout.test.ts`; it must fail before the dialog and actions exist.

- [ ] **Step 3: Implement the dialog**

The dialog must accept open/preview/loading/confirming/error props and emit file selection, target changes, confirm, and close events. It must support target mode/key controls, key-conflict guidance, validation errors, structured diff with text toggle, expiry/stale errors, and disabled in-flight controls. Use existing UI/diff components and semantic design tokens.

- [ ] **Step 4: Wire WorkflowView**

Add request-token guarded preview/confirm state. Preview calls `api.previewWorkflowImport`; confirm calls `api.confirmWorkflowImport`; success closes the dialog, reloads workflows, and selects the result. Export calls `api.exportWorkflow`, downloads `<workflow_key>.workflow.json`, and revokes its object URL in `finally`.

- [ ] **Step 5: Verify and commit**

Run `cd frontend/capabilities && npx tsx --test tests/*.test.ts && npm run typecheck && npm run build`, then commit the dialog, view, and tests with `git commit -m "feat: add workflow import export UI"`.

## Task 6: Full verification and acceptance review

**Files:** No new implementation files.

- [ ] **Step 1: Run backend regression**

Run `pytest -q --ignore=tests/test_capability_delete.py`; all backend tests must pass, with the known pre-existing capability-delete collection issue excluded.

- [ ] **Step 2: Run frontend regression**

Run `cd frontend/capabilities && npx tsx --test tests/*.test.ts && npm run typecheck && npm run build`; all checks must pass.

- [ ] **Step 3: Check migration and worktree hygiene**

Run `git diff --check HEAD~6..HEAD` and `git status --short --branch`; there must be no whitespace errors and no unintended changes.

- [ ] **Step 4: Verify acceptance scenarios**

1. Save v1, edit v2, restore v1, observe v3 with source `restore`.
2. Export a workflow and import under a new key, observe v1 with source `import`.
3. Import changed content into an existing workflow, review current→incoming diff, confirm, observe the next revision with source `import`.
4. Preview overwrite, edit target, confirm, and receive a stale-preview conflict without mutation.
5. Try new import with an existing key and no explicit overwrite/replacement key, and receive a key-conflict prompt.

- [ ] **Step 5: Commit only concrete final fixes**

Run `git status --short && git diff --check`; commit only if review produced a focused fix.
