# Workflow Restore and Import/Export Design

**Date:** 2026-07-18  
**Status:** Approved design

## Goal

Add first-class workflow revision restore and workflow definition import/export. Imported definitions can create a new workflow or preview and logically overwrite an existing workflow after the user reviews a version diff. Every workflow revision records whether it came from editing, importing, or restoring.

## Decisions

### Revision source

Add a required `source` field to workflow definition revisions:

- `edit`: normal editor save; legacy rows are migrated with this value.
- `import`: confirmed workflow definition import.
- `restore`: restoring a historical revision.

Existing `created_by` and `created_at` remain the audit identity and timestamp. Revision numbers remain append-only. Restoring an old snapshot creates a new revision; it never moves the current pointer backward or deletes history.

### Restore

Expose:

```text
POST /workflows/{workflow_key}/revisions/{revision_no}/restore
```

The service loads and validates the stored snapshot, saves it through the normal workflow upsert transaction with `source="restore"`, and returns the new current workflow plus the created revision metadata. The frontend exposes a confirmation action on workflow revision history.

### Export

Expose:

```text
GET /workflows/{workflow_key}/export
```

The downloaded JSON is a versioned transport envelope containing the current workflow definition and export metadata. It excludes runs, queued tasks, execution artifacts, and node execution results. The envelope is designed to be stable enough for later format evolution.

### Import preview and confirmation

Expose two steps:

```text
POST /workflows/import/preview
POST /workflows/import/confirm
```

Preview accepts an uploaded export file and an optional target workflow key. It normalizes and validates the envelope, determines whether the operation creates a new workflow or targets an existing workflow, and persists a short-lived import session. For an existing target it records the target revision used for the preview and returns both structured and unified diff data.

Confirmation references the import session. It rechecks the target's current revision before saving, so a workflow changed after preview cannot be silently overwritten. A new workflow receives the imported key by default; if that key already exists, the user must provide another key or explicitly choose overwrite. Confirmation saves using `source="import"` and creates a new revision when the imported content differs.

The import session is actor-bound, expires, and is single-use. No imported file is applied during preview.

### Frontend flow

- Workflow history displays revision source, creator, and time.
- Workflow history adds “恢复此版本”, with confirmation and a refresh after success.
- Workflow detail adds export and import actions.
- Import dialog supports file selection, target mode/key, preview validation, and explicit confirmation.
- Existing-workflow import shows the structured diff by default and allows switching to unified text diff.
- Import errors, validation errors, key conflicts, stale previews, and expired sessions are shown without changing the current workflow.

## Data flow

```text
Editor save ───────────────┐
Restore revision snapshot ──┼─> validated workflow upsert ─> new revision(source)
Import confirmation ───────┘

Export current definition -> transport envelope -> preview/validate -> diff existing target -> confirm -> import revision
```

## Files and boundaries

- `storage/schema.py`: add revision source and import-session storage with idempotent migration.
- `storage/repositories/workflows.py`: revision source CRUD and import-session CRUD.
- `automation/workflows/service.py`: source-aware save, restore, export envelope, import preview/confirm, stale-session checks.
- `api/routes/workflows.py` and `api/schemas.py`: restore/export/import contracts.
- `frontend/capabilities/src/api/types.ts` and `api/client.ts`: transport types and calls.
- `frontend/capabilities/src/components/version/RevisionHistoryPanel.vue`: source display and restore action.
- `frontend/capabilities/src/components/workflow/WorkflowImportDialog.vue`: import mode, preview, diff, and confirmation.
- `frontend/capabilities/src/views/workflow/WorkflowView.vue`: export/import entry points and dialog wiring.

## Error handling and safety

- All restore/import confirmation writes are transactional with revision archival.
- Imported definitions pass the existing workflow validator before preview and again before confirmation.
- Existing target confirmation fails on revision mismatch and requires a fresh preview.
- Unknown transport format versions are rejected with a clear error.
- Import never mutates execution history or artifacts.
- Restore/import use existing admin authorization.

## Testing

Backend tests cover source values, legacy migration default, restore append semantics, export envelope, new-workflow import, existing-workflow preview diff, confirmation, conflict, stale preview, expiry, and invalid payloads. Frontend tests cover source rendering, restore confirmation, import mode selection, preview diff, and confirm/error states. Existing versioning and regression suites remain unchanged.

## Out of scope

- Importing revision history itself.
- Importing workflow runs, task queue state, or artifacts.
- Rollback of scripts or skills in this change.
- Incremental execution based on workflow revision diff; that remains a separate scheduler feature.
