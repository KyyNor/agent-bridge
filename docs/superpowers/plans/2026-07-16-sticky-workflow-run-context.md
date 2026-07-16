# Sticky Workflow Run Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep batch progress visible on the workflow task page and make Agent switching reusable and visible on every workflow run detail page.

**Architecture:** Extract the Agent output header into `AgentRunTabs.vue`. The task route owns a sticky batch context containing progress plus Agent tabs; the standalone progress route owns a sticky Agent context only. `WorkflowRunDetailPanel.vue` keeps a default header for other callers and gains an opt-out prop so the two routes can place the shared header in their own sticky shell.

**Tech Stack:** Vue 3, TypeScript, Tailwind utility classes, native Node test runner.

---

### Task 1: Add layout contract tests

**Files:**
- Create: `frontend/capabilities/tests/workflowRunContextLayout.test.ts`

- [x] Add source-level assertions that the shared Agent header exists, exposes sticky styling, and the detail panel supports hiding its embedded header.
- [x] Add assertions that `WorkflowView.vue` has separate sticky shells for the batch task context and standalone progress context, and routes both through `AgentRunTabs`.
- [x] Run `npx tsx --test tests/workflowRunContextLayout.test.ts` and confirm it fails because the shared component and route shells do not exist yet.

### Task 2: Extract the reusable Agent header

**Files:**
- Create: `frontend/capabilities/src/components/AgentRunTabs.vue`
- Modify: `frontend/capabilities/src/components/WorkflowRunDetailPanel.vue`

- [x] Move the Agent output count, Agent buttons, refresh button, and detail error banner into `AgentRunTabs.vue`; keep `select-agent-run` and `refresh` as its only events.
- [x] Give the component a `sticky` boolean defaulting to `true`; when true use `sticky top-0 z-30` with an opaque/translucent background, and when false render as a normal flow header for a parent sticky shell.
- [x] Add `showHeader` to `WorkflowRunDetailPanel.vue`, defaulting to `true`, and render `AgentRunTabs` only when enabled. Keep the existing timeline and sub-agent detail body unchanged.
- [x] Run the layout test after extraction and confirm the shared component contract passes.

### Task 3: Add route-specific sticky shells

**Files:**
- Modify: `frontend/capabilities/src/views/workflow/WorkflowView.vue`

- [x] In the `tasks` route, wrap the batch summary/progress block and `AgentRunTabs` in one `workflow-batch-run-context sticky top-0 z-30` shell; pass `:sticky="false"` to the tabs and `:show-header="false"` to the detail panel body.
- [x] In the `progress` route, add a separate `workflow-progress-agent-context sticky top-0 z-30` shell containing `AgentRunTabs`; pass `:show-header="false"` to the detail panel body.
- [x] Keep the existing batch counters, Agent selection handlers, refresh handlers, timeline data, and task queue behavior unchanged.
- [x] Run the layout test and the full frontend suite; expect all layout and existing tests to pass.

### Task 4: Verify and hand off

**Files:**
- Modify: `docs/superpowers/plans/2026-07-16-sticky-workflow-run-context.md`

- [x] Run `npx tsx --test tests/*.test.ts` from `frontend/capabilities`.
- [x] Run `scripts/test.sh fast` from the repository root.
- [x] Inspect the diff for only the shared Agent header, route shells, tests, and plan document; exclude generated `tsconfig.tsbuildinfo` changes.
- [ ] Commit with `feat: keep workflow run context visible`.
