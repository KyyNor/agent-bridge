# 代码仓库详情二级页面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将代码仓库详情从 Dialog 迁移为 `#code-repos/<repo_key>` 二级页面，并保持现有四个详情 Tab、Understand Dashboard 与仓库列表行为不变。

**Architecture:** `App.vue` 将 `subRoute` 传给 `CodeRepoView`；`CodeRepoView` 只负责列表、路由控制和现有短操作弹窗；新增 `CodeRepoDetailView.vue` 承接详情加载、四个 Tab、Understand Dashboard 和全屏状态。父组件通过 `back` 事件统一返回列表。

**Tech Stack:** Vue 3 <script setup>, TypeScript, Tailwind CSS utility classes, Node `node:test`, Vite/Vue TS checker。

## Global Constraints

- 不新增后端接口，不改变现有代码仓库 API 参数和返回处理。
- `#code-repos` 保持列表，`#code-repos/<repo_key>` 表示详情。
- 添加/编辑仓库和能力平面分配继续使用 Dialog。
- 详情主体不使用 Dialog 的 `max-h-[85vh]`，必须可在页面内滚动。
- 离开详情必须停止 Understand Dashboard 的触达定时器。

---

### Task 1: 建立路由和详情布局的失败测试

**Files:**
- Modify: `frontend/capabilities/tests/navigation.test.ts`
- Create: `frontend/capabilities/tests/codeRepoDetailLayout.test.ts`
- Test target: `frontend/capabilities/src/App.vue`, `frontend/capabilities/src/lib/navigation.ts`, `frontend/capabilities/src/views/knowledge/CodeRepoView.vue`, `frontend/capabilities/src/views/knowledge/CodeRepoDetailView.vue`

**Interfaces:**
- `shouldShowPageHeader('code-repos', '')` remains `true`.
- `shouldShowPageHeader('code-repos', 'repo-a')` becomes `false`.
- The source-structure test describes the required route wiring before production code exists.

- [ ] **Step 1: Add the navigation expectation**

Append this case to `navigation.test.ts`:

~~~ts
test('shouldShowPageHeader hides the title on code repository detail routes', () => {
  assert.equal(shouldShowPageHeader('code-repos', 'repo-a'), false)
  assert.equal(shouldShowPageHeader('code-repos', ''), true)
})
~~~

- [ ] **Step 2: Add the failing source-structure test**

Create `codeRepoDetailLayout.test.ts`:

~~~ts
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const read = (path: string) => readFileSync(`${root}/src/${path}`, 'utf8')

test('wires code repository detail as a route-driven independent component', () => {
  const app = read('App.vue')
  const navigation = read('lib/navigation.ts')
  const list = read('views/knowledge/CodeRepoView.vue')
  const detail = read('views/knowledge/CodeRepoDetailView.vue')

  assert.match(app, /<CodeRepoView[^>]*:route-key="subRoute"/)
  assert.match(navigation, /'code-repos'/)
  assert.match(list, /CodeRepoDetailView/)
  assert.match(list, /code-repos\//)
  assert.doesNotMatch(list, /<!-- Repo Detail Dialog -->/)
  assert.match(detail, /defineEmits/)
  assert.match(detail, /@click="goBack"|function goBack/)
  assert.match(detail, /概览/)
  assert.match(detail, /查询/)
  assert.match(detail, /探索/)
  assert.match(detail, /理解/)
  assert.match(detail, /overflow-y-auto/)
})
~~~

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

~~~bash
cd frontend/capabilities
node --import tsx --test tests/navigation.test.ts tests/codeRepoDetailLayout.test.ts
~~~

Expected: the navigation test fails because `code-repos` is not yet a hidden detail route, and the structure test cannot find `CodeRepoDetailView.vue`. Fix only test typos if needed; do not add production code before observing this failure.

---

### Task 2: Extract the detail component and connect the hash route

**Files:**
- Modify: `frontend/capabilities/src/App.vue`
- Modify: `frontend/capabilities/src/lib/navigation.ts`
- Modify: `frontend/capabilities/src/views/knowledge/CodeRepoView.vue`
- Create: `frontend/capabilities/src/views/knowledge/CodeRepoDetailView.vue`
- Test: `frontend/capabilities/tests/navigation.test.ts`, `frontend/capabilities/tests/codeRepoDetailLayout.test.ts`

**Interfaces:**
- `CodeRepoView` receives `{ routeKey: string }`.
- `CodeRepoDetailView` receives `{ repoKey: string, repo: CodeRepository | null }`.
- `CodeRepoDetailView` emits `back`.

- [ ] **Step 1: Add the route prop and parent mode**

In `App.vue`, pass `:route-key="subRoute"` to `CodeRepoView`. In `navigation.ts`, include `code-repos` in the detail route list. In `CodeRepoView.vue`, add:

~~~ts
const props = defineProps<{ routeKey: string }>()
const mode = computed<'list' | 'detail'>(() => (props.routeKey ? 'detail' : 'list'))
const detailRepo = computed(() => repos.value.find(repo => repo.repo_key === props.routeKey) || null)

function openDetail(repo: CodeRepository) {
  window.location.hash = `code-repos/${repo.repo_key}`
}

function backToList() {
  window.location.hash = 'code-repos'
}
~~~

Watch `props.routeKey` and `repos` so a directly opened route loads after the list request and a deleted repository is represented by `repo === null`.

- [ ] **Step 2: Move detail state and methods into `CodeRepoDetailView.vue`**

Move the current detail-only refs, computed values, API methods, Understand Dashboard theme helpers, dashboard touch timer, `onBeforeUnmount` cleanup, and the `Repo Detail Dialog` body into the new component. Keep these API calls and their existing behavior:

~~~ts
api.getCodeGraphStatus()
api.getRepoOverview(repo.repo_key)
api.queryRepo(repo.repo_key, query)
api.exploreRepo(repo.repo_key, exploreQuery)
api.checkUAAvailability(repo.repo_key)
api.getUAStatus(repo.repo_key)
api.getUASummary(repo.repo_key)
api.getSchedulerStatus()
api.startUADashboard(repo.repo_key)
api.touchUADashboard(repo.repo_key)
~~~

The detail component must reset all repo-specific state when `repoKey` changes, stop the touch timer before resetting it, and emit `back` from the top return button. Render the detail as a page section with a scrollable body rather than `DialogContent`.

- [ ] **Step 3: Replace list rendering and remove the old detail Dialog**

Wrap the existing list template in `v-if="mode === 'list'`. Replace the list row’s detail action with `@click="openDetail(r)"`. Render the detail component for the detail mode:

~~~vue
<CodeRepoDetailView
  v-else
  :repo-key="props.routeKey"
  :repo="detailRepo"
  @back="backToList"
/>
~~~

Leave the add/edit repo Dialog and plane assignment Dialog in the list branch. Delete the old repo detail Dialog and its fullscreen Teleport from `CodeRepoView.vue`; the fullscreen Teleport moves with the detail component.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run:

~~~bash
cd frontend/capabilities
node --import tsx --test tests/navigation.test.ts tests/codeRepoDetailLayout.test.ts
~~~

Expected: all focused route and structure tests pass.

---

### Task 3: Verify data lifecycle, error states, and existing behaviors

**Files:**
- Modify: `frontend/capabilities/src/views/knowledge/CodeRepoDetailView.vue`
- Modify: `frontend/capabilities/src/views/knowledge/CodeRepoView.vue`
- Test: `frontend/capabilities/tests/codeRepoDetailLayout.test.ts`

- [ ] **Step 1: Add lifecycle assertions**

Extend the structure test to assert that the detail component contains a repository-not-found message, the `back` emit, `onBeforeUnmount`, and the dashboard timer cleanup path:

~~~ts
assert.match(detail, /仓库不存在|无法加载该仓库/)
assert.match(detail, /onBeforeUnmount/)
assert.match(detail, /stopTouchTimer/)
assert.match(list, /detailRepo/)
~~~

- [ ] **Step 2: Implement invalid-route and loading states**

When `repo` is null, show a page-level error with a link/button that emits `back`; do not call repository-specific APIs. When the initial overview/status requests settle, show the same overview, stats, and tab content the Dialog showed. Keep per-tab error messages local to the corresponding section.

- [ ] **Step 3: Run focused and existing frontend tests**

Run:

~~~bash
cd frontend/capabilities
node --import tsx --test tests/navigation.test.ts tests/codeRepoDetailLayout.test.ts tests/deleteConfirmGuards.test.ts tests/pagination.test.ts
~~~

Expected: all selected tests pass with exit code 0.

---

### Task 4: Typecheck and production build

**Files:**
- No additional source files; inspect the complete diff and generated output only.

- [ ] **Step 1: Run the full frontend typecheck**

Run `cd frontend/capabilities && npm run typecheck`. Expected: exit code 0 and no TypeScript/Vue diagnostics.

- [ ] **Step 2: Run the production build**

Run `cd frontend/capabilities && npm run build`. Expected: exit code 0 and a successful Vite build in `src/agent_bridge/static/capabilities/`.

- [ ] **Step 3: Review the final diff**

Run:

~~~bash
git diff --check
git status --short
git diff --stat
~~~

Confirm only the route wiring, code repository list/detail components, focused tests, and implementation documentation changed; no backend or unrelated UI files changed.
