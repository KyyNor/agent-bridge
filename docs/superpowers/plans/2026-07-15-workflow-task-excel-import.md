# Workflow Task Excel Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a server-validated .xlsx import flow inside the existing workflow task-progress page, with preview, confirmation, duplicate handling, and atomic task writes.

**Architecture:** Parse the first worksheet on the server, normalize each row into the existing task contract {task_key, task_version, type, payload}, and save a short-lived preview snapshot in SQLite. The confirm endpoint rechecks current task state and atomically applies the snapshot using the same status semantics as upsert_workflow_tasks. The Vue task-progress view owns the dialog state and refreshes the existing task list after confirmation.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite, openpyxl, pytest, Vue 3, TypeScript, Tailwind/shadcn-vue.

---

## Existing context and constraints

- Persisted task columns are defined in src/agent_bridge/storage/schema.py. Business inputs are task_key, task_version, type, and JSON payload; uniqueness is (workflow_key, task_key, task_version).
- WorkflowService.set_tasks_for_agent() already validates the same task inputs before calling SQLiteStore.upsert_workflow_tasks().
- Existing upsert semantics are authoritative: pending, failed, abandoned, and expired running rows are updated; valid active running leases are skipped; completed is skipped unless its configured rerun window has expired.
- The task page is the routeMode === 'tasks' branch of frontend/capabilities/src/views/workflow/WorkflowView.vue.
- Admin authorization and domain-error mapping already exist in src/agent_bridge/api/app.py.
- Baseline in this worktree: PYTHONPATH=. uv run pytest -q -m "not ragflow and not weknora" passes with 782 tests, 8 deselected, and one existing deprecation warning after the frontend build. The unfiltered suite requires unavailable external RagFlow/Weknora services.

## File map

Create:
- src/agent_bridge/automation/workflows/task_import.py — xlsx parser, row normalization, validation, and template generation.
- tests/test_workflow_task_import.py — parser/template unit tests.
- tests/test_workflow_task_import_storage.py — snapshot and atomic confirmation tests.
- frontend/capabilities/src/views/workflow/WorkflowTaskImportDialog.vue — import dialog presentation.

Modify:
- pyproject.toml and uv.lock — declare openpyxl directly.
- src/agent_bridge/storage/schema.py — add workflow_task_imports.
- src/agent_bridge/storage/repositories/workflows.py and src/agent_bridge/storage/sqlite.py — add snapshot methods and shared task-action logic.
- src/agent_bridge/automation/workflows/service.py — add admin-guarded preview, confirm, and template methods.
- src/agent_bridge/api/schemas.py and src/agent_bridge/api/routes/workflows.py — add request model and three endpoints.
- tests/test_workflow_storage.py and tests/test_workflow_api.py — preserve semantics and cover the API.
- frontend/capabilities/src/api/types.ts and frontend/capabilities/src/api/client.ts — add typed import calls.
- frontend/capabilities/src/views/workflow/WorkflowView.vue — add toolbar buttons, dialog state, orchestration, and refresh.

---

### Task 1: Build the xlsx parser and template generator with tests first

Files:
- Create: src/agent_bridge/automation/workflows/task_import.py
- Create: tests/test_workflow_task_import.py
- Modify: pyproject.toml
- Modify: uv.lock

- [ ] Step 1: Write failing parser tests.

Use openpyxl to create in-memory workbooks. Cover fixed-column mapping, extra columns becoming payload, first-sheet-only behavior, blank-row skipping, date normalization, missing/duplicate headers, the 5,000-row limit, blank task keys, and duplicate task keys within one file.

The valid-row assertion should be:

~~~
result = parse_task_import(
    workbook_bytes(
        ["task_key", "task_version", "type", "repo", "priority"],
        [[" owner/repo ", "v1", "repo", "owner/repo", 3]],
        second_sheet_rows=[["ignored"]],
    ),
    filename="tasks.xlsx",
)
assert result.sheet_name == "tasks"
assert result.rows[0].task_key == "owner/repo"
assert result.rows[0].task_version == "v1"
assert result.rows[0].task_type == "repo"
assert result.rows[0].payload == {"repo": "owner/repo", "priority": 3}
assert result.rows[0].errors == ()
~~~

The invalid-row test must assert that a blank key gets task_key 不能为空, and that the second occurrence of the same task_key plus task_version gets a duplicate error while the first remains valid.

- [ ] Step 2: Run the focused tests and verify they fail.

~~~
PYTHONPATH=. uv run pytest -q tests/test_workflow_task_import.py
~~~

Expected: collection fails because the parser module does not exist.

- [ ] Step 3: Implement the parser contract.

Define these public names:

~~~
MAX_IMPORT_ROWS = 5000
SUPPORTED_IMPORT_EXTENSION = ".xlsx"

class TaskImportFormatError(ValueError):
    """The workbook cannot produce a valid task import preview."""

@dataclass(frozen=True)
class ParsedTaskImportRow:
    row_number: int
    task_key: str
    task_version: str
    task_type: str
    payload: dict[str, Any]
    errors: tuple[str, ...]

@dataclass(frozen=True)
class ParsedTaskImport:
    filename: str
    sheet_name: str
    rows: tuple[ParsedTaskImportRow, ...]

def parse_task_import(content: bytes, *, filename: str) -> ParsedTaskImport:
    raise NotImplementedError

def build_task_import_template() -> bytes:
    raise NotImplementedError
~~~

Use load_workbook(BytesIO(content), read_only=True, data_only=True) and read only workbook.worksheets[0]. Catch openpyxl invalid-file and zip errors and translate them to TaskImportFormatError. Trim BOM and whitespace from headers, compare fixed headers case-insensitively, reject duplicate normalized headers, and require task_key. Ignore rows where every cell is empty. Keep strings, integers, floats, and booleans as JSON scalars; convert date/time values using isoformat(). Extra columns become payload keys and fixed columns never enter payload. A non-empty cell under an empty extra-column header is a row error.

Use Excel row numbers in all row errors. Detect duplicate task_key plus task_version pairs after normalization. Keep invalid rows in the parsed result for preview, but the service excludes them from the confirmable snapshot. Generate a template with a first tasks sheet containing only task_key, task_version, type, and a second 说明 sheet explaining that extra columns become payload fields.

- [ ] Step 4: Add the dependency and make parser tests pass.

Add openpyxl>=3.1.0,<4 to runtime dependencies, run uv lock, then run the focused parser test command again. Expected: all parser and template tests pass.

- [ ] Step 5: Commit the parser slice.

~~~
git add pyproject.toml uv.lock src/agent_bridge/automation/workflows/task_import.py tests/test_workflow_task_import.py
git commit -m "feat: add workflow task xlsx parser"
~~~
+

### Task 2: Add import snapshots and share task-action classification with existing upsert

Files:
- Modify: src/agent_bridge/storage/schema.py
- Modify: src/agent_bridge/storage/repositories/workflows.py
- Modify: src/agent_bridge/storage/sqlite.py
- Create: tests/test_workflow_task_import_storage.py
- Modify: tests/test_workflow_storage.py

- [ ] Step 1: Write failing storage tests.

Create a store/profile/workflow helper using wm_paths. Test that preview classifies a new row as created and an existing pending row as updated; confirmation creates the task and changes the snapshot to confirmed; expired snapshots are rejected; a different actor cannot confirm a snapshot; and a valid running lease is rechecked at confirmation and skipped.

The preview assertion should be:

~~~
preview = store.preview_workflow_task_actions(
    "page-report",
    [
        {"task_key": "task:a", "payload": {"v": 2}},
        {"task_key": "task:new", "payload": {"v": 3}},
    ],
)
assert [row["action"] for row in preview["rows"]] == ["updated", "created"]
assert preview["summary"]["updated"] == 1
assert preview["summary"]["created"] == 1
~~~

Add a regression assertion to tests/test_workflow_storage.py that completed-task and rerun-window counts remain unchanged after the refactor.

- [ ] Step 2: Run storage tests and verify the new tests fail.

~~~
PYTHONPATH=. uv run pytest -q tests/test_workflow_task_import_storage.py tests/test_workflow_storage.py
~~~

Expected: the new table and repository methods do not exist.

- [ ] Step 3: Add the snapshot schema and store facade.

Append this SQL to WORKFLOW_SCHEMA:

~~~
CREATE TABLE IF NOT EXISTS workflow_task_imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id TEXT NOT NULL UNIQUE,
  workflow_key TEXT NOT NULL REFERENCES workflow_definitions(workflow_key) ON DELETE CASCADE,
  actor TEXT NOT NULL,
  filename TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  tasks_json TEXT NOT NULL DEFAULT '[]',
  preview_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'previewed',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_task_imports_expiry
  ON workflow_task_imports(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_workflow_task_imports_workflow
  ON workflow_task_imports(workflow_key, created_at DESC);
~~~

Expose these methods through SQLiteStore: preview_workflow_task_actions, create_workflow_task_import, get_workflow_task_import, confirm_workflow_task_import, and delete_expired_workflow_task_imports. The facade methods only delegate to self.workflows.

- [ ] Step 4: Refactor repository writes around one classifier and one transaction.

Add private helpers with these signatures:

~~~
def _workflow_task_action(
    self,
    *,
    existing: sqlite3.Row | None,
    now: datetime,
    rerun_cutoff: datetime,
) -> str:
    raise NotImplementedError

def _apply_workflow_tasks(
    self,
    conn: sqlite3.Connection,
    workflow_key: str,
    tasks: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, int]:
    raise NotImplementedError
~~~

Replace the temporary NotImplementedError bodies with the implementation during this step. The classifier must return only created, updated, skipped_completed, skipped_running, or reopened_expired using the current lease and rerun rules. Make existing upsert_workflow_tasks() and the new confirmation method call _apply_workflow_tasks(). The read-only preview method uses the same classifier and returns per-row action plus aggregate counts.

Implement confirmation inside one repository connection: select the snapshot, verify workflow, actor, status, and expiration; decode tasks_json; call _apply_workflow_tasks(); update status=confirmed and confirmed_at; return a dictionary containing import_id plus all count keys. Any exception must roll back both task writes and the snapshot status. Remove pending import snapshots when clear_workflow_execution_data() clears a workflow, while preserving the existing clear response keys.

- [ ] Step 5: Run storage tests and commit.

~~~
PYTHONPATH=. uv run pytest -q tests/test_workflow_task_import_storage.py tests/test_workflow_storage.py
git add src/agent_bridge/storage/schema.py src/agent_bridge/storage/repositories/workflows.py src/agent_bridge/storage/sqlite.py tests/test_workflow_task_import_storage.py tests/test_workflow_storage.py
git commit -m "feat: stage workflow task imports atomically"
~~~

### Task 3: Expose preview, confirmation, and template download through the backend API

Files:
- Modify: src/agent_bridge/automation/workflows/service.py
- Modify: src/agent_bridge/api/schemas.py
- Modify: src/agent_bridge/api/routes/workflows.py
- Modify: tests/test_workflow_api.py

- [ ] Step 1: Write failing API tests.

Use an in-memory xlsx byte helper and cover admin preview, admin confirmation, row-level errors with can_confirm=false, non-xlsx/corrupt/over-limit files, non-admin access to all three routes, second confirmation, cross-workflow confirmation, and template media type plus Content-Disposition.

The happy-path assertion should be:

~~~
preview_response = client.post(
    "/workflows/page-report/tasks/import/preview",
    headers={"X-Agent-Bridge-User": "root"},
    files={"file": ("tasks.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
)
assert preview_response.status_code == 200
preview = preview_response.json()
assert preview["can_confirm"] is True
assert preview["summary"]["created"] == 1

confirm_response = client.post(
    "/workflows/page-report/tasks/import/confirm",
    headers={"X-Agent-Bridge-User": "root"},
    json={"import_id": preview["import_id"]},
)
assert confirm_response.status_code == 200
assert confirm_response.json()["created"] == 1
~~~

- [ ] Step 2: Run API import tests and verify they fail.

~~~
PYTHONPATH=. uv run pytest -q tests/test_workflow_api.py -k import
~~~

Expected: the request model and routes are not defined.

- [ ] Step 3: Add the request model and service methods.

Add:

~~~
class WorkflowTaskImportConfirmRequest(BaseModel):
    import_id: str
~~~

Add service methods with concrete signatures:

~~~
def preview_task_import(
    self,
    *,
    actor: str,
    workflow_key: str,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    raise NotImplementedError

def confirm_task_import(
    self,
    *,
    actor: str,
    workflow_key: str,
    import_id: str,
) -> dict[str, Any]:
    raise NotImplementedError

def build_task_import_template(
    self,
    *,
    actor: str,
    workflow_key: str,
) -> bytes:
    raise NotImplementedError
~~~

Replace the temporary bodies during this step. Each method must call require_admin_user() and verify the workflow exists. Preview first deletes expired previewed snapshots, rejects non-.xlsx filenames, translates TaskImportFormatError to ValidationError, excludes invalid rows from tasks_json, classifies valid rows against current state, creates a UUID-backed snapshot with a 30-minute expiry, and returns the report. If no valid rows remain, set can_confirm=false. Confirmation delegates to the atomic store method. Template generation delegates to the parser module.

- [ ] Step 4: Add routes using existing domain-error handling.

Import File, Response, and UploadFile. Add these endpoints before the existing parameterized task routes:

~~~
@router.get("/workflows/{workflow_key}/tasks/import/template")
def download_task_import_template(workflow_key: str, current_actor: str = Depends(actor)) -> Response:
    content = service.workflows.build_task_import_template(actor=current_actor, workflow_key=workflow_key)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="workflow-task-template.xlsx"'},
    )

@router.post("/workflows/{workflow_key}/tasks/import/preview")
async def preview_task_import(
    workflow_key: str,
    file: UploadFile = File(...),
    current_actor: str = Depends(actor),
) -> dict[str, Any]:
    return service.workflows.preview_task_import(
        actor=current_actor,
        workflow_key=workflow_key,
        filename=file.filename or "",
        content=await file.read(),
    )

@router.post("/workflows/{workflow_key}/tasks/import/confirm")
def confirm_task_import(
    workflow_key: str,
    payload: WorkflowTaskImportConfirmRequest,
    current_actor: str = Depends(actor),
) -> dict[str, Any]:
    return service.workflows.confirm_task_import(
        actor=current_actor,
        workflow_key=workflow_key,
        import_id=payload.import_id,
    )
~~~

- [ ] Step 5: Run API tests and commit.

~~~
PYTHONPATH=. uv run pytest -q tests/test_workflow_api.py -k import
git add src/agent_bridge/automation/workflows/service.py src/agent_bridge/api/schemas.py src/agent_bridge/api/routes/workflows.py tests/test_workflow_api.py
git commit -m "feat: expose workflow task import api"
~~~
+

### Task 4: Add typed frontend API calls and the import dialog

Files:
- Modify: frontend/capabilities/src/api/types.ts
- Modify: frontend/capabilities/src/api/client.ts
- Create: frontend/capabilities/src/views/workflow/WorkflowTaskImportDialog.vue

- [ ] Step 1: Add TypeScript response types.

Add these exact interfaces to frontend/capabilities/src/api/types.ts:

~~~
export interface WorkflowTaskImportRow {
  row_number: number
  task_key: string
  task_version: string
  type: string
  payload: Record<string, unknown>
  action: 'created' | 'updated' | 'skipped_running' | 'skipped_completed' | 'reopened_expired' | 'error'
  errors: string[]
}

export interface WorkflowTaskImportSummary {
  total_rows: number
  valid_rows: number
  invalid_rows: number
  created: number
  updated: number
  skipped_running: number
  skipped_completed: number
  reopened_expired: number
}

export interface WorkflowTaskImportPreview {
  import_id: string
  filename: string
  sheet_name: string
  expires_at: string
  can_confirm: boolean
  summary: WorkflowTaskImportSummary
  rows: WorkflowTaskImportRow[]
}

export interface WorkflowTaskImportResult {
  import_id: string
  created: number
  updated: number
  skipped_running: number
  skipped_completed: number
  reopened_expired: number
}
~~~

- [ ] Step 2: Add API methods.

Add a getBlob() helper in client.ts that uses the standard user header and returns response.blob(). Add:

~~~
downloadWorkflowTaskTemplate: (workflowKey: string) =>
  getBlob('/workflows/' + workflowKey + '/tasks/import/template'),

previewWorkflowTaskImport: (workflowKey: string, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return postFormData<WorkflowTaskImportPreview>(
    '/workflows/' + workflowKey + '/tasks/import/preview',
    form,
  )
},

confirmWorkflowTaskImport: (workflowKey: string, importId: string) =>
  post<WorkflowTaskImportResult>(
    '/workflows/' + workflowKey + '/tasks/import/confirm',
    { import_id: importId },
  ),
~~~

- [ ] Step 3: Create the dialog component.

Create WorkflowTaskImportDialog.vue with props open, preview, loading, confirming, and error; emit update:open, select-file, download-template, and confirm. Render an xlsx file input, template-download button, loading/error states, summary cards, a scrollable row table, collapsible payload details, and a confirm button disabled unless preview.can_confirm is true and no request is active. Use the existing Dialog, Button, Badge, and Input components and no new frontend dependency.

- [ ] Step 4: Run typecheck and commit.

~~~
npm --prefix frontend/capabilities run typecheck
git add frontend/capabilities/src/api/types.ts frontend/capabilities/src/api/client.ts frontend/capabilities/src/views/workflow/WorkflowTaskImportDialog.vue
git commit -m "feat: add workflow task import frontend api"
~~~

### Task 5: Integrate the dialog into the existing task-progress page

Files:
- Modify: frontend/capabilities/src/views/workflow/WorkflowView.vue

- [ ] Step 1: Add parent state and orchestration.

Add refs for showTaskImport, taskImportPreview, taskImportLoading, taskImportConfirming, and taskImportError. Implement openTaskImport(), closeTaskImport(), previewTaskImport(file), confirmTaskImport(), and downloadTaskImportTemplate(). Use the existing errorMessage() helper and clear loading flags in finally. Confirmation calls the API with the current import_id, closes/resets the dialog, clears selected task IDs, updates batchSummary with counts, and reloads the current workflow tasks. Template download creates a temporary object URL, clicks a temporary anchor, and revokes the URL.

- [ ] Step 2: Add toolbar buttons only to the tasks route.

Beside the existing task refresh button in the routeMode === 'tasks' toolbar, add outline “下载模板” and primary “导入 Excel” buttons. Disable them when no task workflow exists; disable import during batch actions. Do not add these controls to workflow detail, progress, or edit routes.

- [ ] Step 3: Mount the dialog and wire events.

Import WorkflowTaskImportDialog and render it near the existing dialogs with v-model:open, the preview/loading/error props, and the four event handlers. Closing resets only import state; it does not clear the current task list. Successful confirmation keeps the same hash route and refreshes the list.

- [ ] Step 4: Run frontend checks and commit.

~~~
npm --prefix frontend/capabilities run typecheck
npm --prefix frontend/capabilities run build
git add frontend/capabilities/src/views/workflow/WorkflowView.vue
git commit -m "feat: add excel import to workflow task page"
~~~

### Task 6: Add edge-case regression coverage and finish verification

Files:
- Modify focused import, storage, and API test files when an uncovered regression is identified.

- [ ] Step 1: Add explicit edge-case tests.

Cover these scenarios:

1. Preview a pending task, lease it before confirmation, then assert confirmation returns skipped_running and preserves the existing payload.
2. Force a write exception after the first candidate in a multi-row confirmation, then assert neither task exists and the snapshot remains previewed.
3. Expired snapshot confirmation is rejected.
4. clear_workflow_execution_data() removes pending import snapshots.
5. Same task_key + task_version in one workbook blocks confirmation while preserving the first row’s valid preview.
6. A completed task inside the rerun window is skipped; the same task outside the window is reopened.

- [ ] Step 2: Run focused backend tests.

~~~
PYTHONPATH=. uv run pytest -q \
  tests/test_workflow_task_import.py \
  tests/test_workflow_task_import_storage.py \
  tests/test_workflow_storage.py \
  tests/test_workflow_api.py -k "workflow or import"
~~~

Expected: all focused tests pass.

- [ ] Step 3: Run the supported local regression suite and frontend checks.

~~~
PYTHONPATH=. uv run pytest -q -m "not ragflow and not weknora"
npm --prefix frontend/capabilities run typecheck
npm --prefix frontend/capabilities run build
~~~

Expected: zero failures in the filtered backend suite and exit code 0 from both frontend commands. Do not count unavailable RagFlow/Weknora services as local regressions.

- [ ] Step 4: Inspect the final diff and keep generated files out of commits.

~~~
git diff --check
git status --short
git log --oneline -8
~~~

The ignored frontend node_modules, generated Vue output under src/agent_bridge/static/capabilities/, and local environment files must not be committed. The final tracked diff should contain only parser, storage/API, frontend source, tests, and dependency lock changes.

---

## Handoff

Execute the tasks in order. Keep the shared classifier and atomic repository method as the single source of truth for preview and confirmation. Preserve all existing workflow_set_task behavior and task-page batch actions.
