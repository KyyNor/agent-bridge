# Knowledge Folder Resizer Implementation Plan

> **For agentic workers:** Use the approved inline execution flow and keep the changes limited to the knowledge document layout.

**Goal:** Make the knowledge-base folder pane resizable on desktop while preserving the stacked mobile layout.

**Architecture:** Keep the current `KnowledgeView.vue` ownership of the documents layout. Store the pane width locally as a clamped Vue ref, expose a vertical separator that handles pointer dragging and arrow-key adjustments, and pass the width to the desktop flex layout through a CSS custom property. The backend and `FolderTree.vue` remain unchanged.

**Tech Stack:** Vue 3 `<script setup>`, TypeScript, Tailwind CSS utilities, Node test runner.

---

### Task 1: Add the failing layout contract

**Files:**
- Modify: `frontend/capabilities/tests/uploadDialogLayout.test.ts`

- [ ] Add assertions for a 300px default width, 240–420px bounds, resize handlers, the separator role/test id, and the responsive CSS variable width.
- [ ] Run `node --test tests/uploadDialogLayout.test.ts` and verify the new test fails because the implementation does not yet expose the resizer.

### Task 2: Implement the resizable pane

**Files:**
- Modify: `frontend/capabilities/src/views/knowledge/KnowledgeView.vue`

- [ ] Add clamped pane-width state and pointer lifecycle handlers; restore body cursor/text-selection styles on pointer-up and component unmount.
- [ ] Add keyboard support with 10px arrow-key steps and 40px Shift+arrow steps.
- [ ] Replace the fixed `lg:grid-cols-[270px_minmax(0,1fr)]` layout with desktop flex columns: directory pane, 16px separator, and flexible document pane. Keep the existing mobile grid behavior.
- [ ] Run the focused test and verify it passes.

### Task 3: Verify the frontend

**Files:**
- No additional files.

- [ ] Run `node --test tests/uploadDialogLayout.test.ts`.
- [ ] Run `npm run typecheck`.
- [ ] Run `npm run build`.

### Task 4: Commit the feature

**Files:**
- Commit the implementation, regression test, and this plan.

- [ ] Confirm unrelated existing worktree changes are not staged.
- [ ] Commit with `feat(ui): make knowledge folder pane resizable` on `main`.
