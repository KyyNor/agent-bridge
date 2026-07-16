# 能力平面配置二级详情页实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将能力平面配置从弹窗迁移为类似知识库的 `#profiles/<profile_key>` 二级详情页，并让取消/确认操作始终可见。

**Architecture:** `App.vue` 将复合 hash 的 `subRoute` 传入 `ProfilesView`；`ProfilesView` 只负责列表、添加弹窗和路由离开保护；新建 `ProfileDetailView.vue` 承载当前配置弹窗的加载、编辑、保存和详情布局。详情主体独立滚动，底部操作区置于滚动区之外；未保存状态用一个纯函数快照比较器判断。

**Tech Stack:** Vue 3 `<script setup>`, TypeScript, Tailwind CSS utility classes, `node:test`, `tsx`, Vite/Vue TS checker。

---

## 文件边界

- Modify: `frontend/capabilities/src/App.vue` — 将 `subRoute` 传给 `ProfilesView`。
- Modify: `frontend/capabilities/src/lib/navigation.ts` — 隐藏能力平面详情页的顶层页面标题。
- Modify: `frontend/capabilities/src/views/capabilities/ProfilesView.vue` — 保留列表/添加能力平面，增加 hash 路由控制、离开保护和详情组件编排。
- Create: `frontend/capabilities/src/views/capabilities/ProfileDetailView.vue` — 承接原配置 Dialog 的状态、API 调用、详情模板和固定操作栏。
- Create: `frontend/capabilities/src/views/capabilities/profileConfigSnapshot.ts` — 生成可比较的配置快照。
- Modify: `frontend/capabilities/tests/navigation.test.ts` — 增加能力平面详情路由标题断言。
- Create: `frontend/capabilities/tests/profileConfigSnapshot.test.ts` — 验证快照排序和未保存检测。
- Create: `frontend/capabilities/tests/profileDetailLayout.test.ts` — 验证列表/详情接线和固定操作栏结构。

### Task 1: 建立 hash 二级页面并拆出详情组件

**Files:** `App.vue`, `lib/navigation.ts`, `ProfilesView.vue`, `ProfileDetailView.vue`, `navigation.test.ts`

- [ ] **Step 1: 先补路由行为测试**

  在 `frontend/capabilities/tests/navigation.test.ts` 现有详情路由测试中加入：

  ```ts
  assert.equal(shouldShowPageHeader('profiles', 'safe-readonly'), false)
  assert.equal(shouldShowPageHeader('profiles', ''), true)
  ```

  运行：

  ```bash
  cd frontend/capabilities
  node --import tsx --test tests/navigation.test.ts
  ```

  预期：新增的详情断言失败，因为 `navigation.ts` 当前没有把 `profiles` 纳入详情路由集合。

- [ ] **Step 2: 接通父级复合 hash 路由**

  在 `frontend/capabilities/src/App.vue` 将：

  ```vue
  <ProfilesView v-else-if="view === 'profiles'" />
  ```

  改为：

  ```vue
  <ProfilesView v-else-if="view === 'profiles'" :route-key="subRoute" />
  ```

  在 `shouldShowPageHeader` 的详情 key 集合中加入 `'profiles'`，然后重新运行 `node --import tsx --test tests/navigation.test.ts`，预期全部通过。

- [ ] **Step 3: 拆出 `ProfileDetailView.vue` 并保持现有配置业务不变**

  从 `ProfilesView.vue` 移出当前 `showConfig`、`configProfile`、配置加载状态、Pin/记忆/Profile 文档状态，以及 `openConfig` 之后的配置函数和 `<!-- Config Dialog -->` 模板。新组件使用以下接口：

  ```ts
  const props = defineProps<{
    profileKey: string
    profile: ProjectProfile | null
  }>()

  const emit = defineEmits<{
    saved: []
    back: []
    cancel: []
  }>()
  ```

  详情组件在 `watch(() => props.profileKey, ...)` 中重置并加载当前 profile；核心 API 仍调用 `api.catalog()`、`api.listWikiKbs()`、`api.listCodeRepos()`、`api.getProfile()`、`api.listMemoryBlocks()`、`api.getProfileMemory()`、Pin API 和 Profile 文档 API，不新增后端接口。加载失败文案沿用当前错误信息。

  在 `ProfilesView.vue` 保留列表和添加弹窗状态，把列表中的：

  ```vue
  <Button ... @click="openConfig(p)">配置</Button>
  ```

  改为进入路由：

  ```ts
  function openDetail(profile: ProjectProfile) {
    window.location.hash = `profiles/${profile.profile_key}`
  }
  ```

  列表模板在 `routeKey` 为空时显示原列表，否则显示：

  ```vue
  <ProfileDetailView
    ref="detailRef"
    :profile-key="routeKey"
    :profile="profiles.find((item) => item.profile_key === routeKey) || null"
    @back="requestListNavigation"
    @cancel="requestListNavigation"
    @saved="handleDetailSaved"
  />
  ```

  `handleDetailSaved` 先重新调用 `api.listProfiles()`，再把 hash 切回 `profiles`；添加能力平面仍使用原 `showAdd` Dialog。路由控制器的 `watch(() => props.routeKey, ...)` 使用 `flush: 'sync'`，这样浏览器后退将详情 key 清空时，会先完成离开确认，再让详情组件卸载。

- [ ] **Step 4: 运行拆分后的类型检查并提交页面骨架**

  运行：

  ```bash
  cd frontend/capabilities
  npm run typecheck
  ```

  预期：通过，且详情页不再依赖原配置 Dialog 的尺寸/滚动类。

  提交：

  ```bash
  git add src/App.vue src/lib/navigation.ts src/views/capabilities/ProfilesView.vue src/views/capabilities/ProfileDetailView.vue tests/navigation.test.ts
  git commit -m "feat: route capability profile configuration detail"
  ```

### Task 2: 加入固定操作栏和未保存离开保护

**Files:** `profileConfigSnapshot.ts`, `profileConfigSnapshot.test.ts`, `ProfilesView.vue`, `ProfileDetailView.vue`, `profileDetailLayout.test.ts`

- [ ] **Step 1: 先写配置快照的失败测试**

  新建 `frontend/capabilities/tests/profileConfigSnapshot.test.ts`，覆盖无序数组不产生误报、字段改变能检测到、未提交 Notes 能检测到：

  ```ts
  import assert from 'node:assert/strict'
  import test from 'node:test'
  import { profileConfigDraftKey } from '../src/views/capabilities/profileConfigSnapshot.ts'

  const base = {
    sourceRules: [{ source_type: 'mcp_service', source_key: 'docs', effect: 'allow' as const }],
    resourceRules: [{ resource_type: 'wiki_kb', resource_key: 'handbook' }],
    memoryBlockKey: 'team-memory',
    pins: [{ service_key: 'docs', tool_type: 'overview' }],
    pinMode: 'ratio' as const,
    pinRatio: 10,
    pinCount: 3,
    manualNotes: '',
  }

  test('normalizes rule order', () => {
    const reordered = { ...base, sourceRules: [...base.sourceRules].reverse(), resourceRules: [...base.resourceRules].reverse() }
    assert.equal(profileConfigDraftKey(base), profileConfigDraftKey(reordered))
  })

  test('changes when a draft field or manual notes changes', () => {
    assert.notEqual(profileConfigDraftKey(base), profileConfigDraftKey({ ...base, memoryBlockKey: '' }))
    assert.notEqual(profileConfigDraftKey(base), profileConfigDraftKey({ ...base, manualNotes: '只读范围' }))
  })
  ```

  运行：

  ```bash
  cd frontend/capabilities
  node --import tsx --test tests/profileConfigSnapshot.test.ts
  ```

  预期：失败，因为快照 helper 尚未创建。

- [ ] **Step 2: 实现稳定的配置快照 helper**

  在 `frontend/capabilities/src/views/capabilities/profileConfigSnapshot.ts` 定义 `ProfileConfigDraft`，包含 `sourceRules`、`resourceRules`、`memoryBlockKey`、`pins`、`pinMode`、`pinRatio`、`pinCount` 和 `manualNotes`。`profileConfigDraftKey` 返回 JSON 字符串；比较前按以下 key 排序：

  ```ts
  sourceRules: `${source_type}:${source_key}:${effect}`
  resourceRules: `${resource_type}:${resource_key}`
  pins: `${service_key}:${tool_type}`
  ```

  再运行该测试，预期 PASS。

- [ ] **Step 3: 在详情组件中建立初始快照和离开暴露接口**

  `ProfileDetailView.vue` 在核心配置加载完成后保存初始 `ProfileConfigDraft`；Pin 预览首次加载后只补齐初始快照的 Pin 字段，不覆盖用户已经修改的服务、资源或记忆草稿。`hasUnsavedChanges` 使用 `profileConfigDraftKey(initialDraft) !== profileConfigDraftKey(currentDraft)`，Pin 尚未加载时不把 Pin 视为脏数据。

  组件通过 `defineExpose` 暴露：

  ```ts
  defineExpose({
    hasUnsavedChanges,
    isBusy: computed(() => configLoading.value || configSaving.value || pinSaving.value || docSaving.value),
  })
  ```

  “保存 Pin”和“保存手动补充”成功后更新对应初始快照；底部确认成功后整体更新初始快照并发出 `saved`。

- [ ] **Step 4: 将路由离开保护接到返回、取消和浏览器后退**

  `ProfilesView.vue` 保存当前详情 key 和一个“允许离开”标记。顶部返回/底部取消调用同一个 `requestListNavigation`：详情未暴露脏状态时直接写 `window.location.hash = 'profiles'`；有修改时调用：

  ```ts
  const discard = await confirm({
    title: '放弃未保存修改',
    description: '当前能力平面配置有未保存修改，确定离开吗？',
    confirmText: '放弃并返回',
  })
  ```

  只有确认后才设置允许离开标记并切换 hash。监听 `props.routeKey` 从详情 key 变为空时复用同一确认，并使用 `flush: 'sync'` 确保详情 ref 仍可读取；取消时将 hash 恢复为 `profiles/<previousKey>`，确认时保持列表路由。

- [ ] **Step 5: 把详情页改成主体滚动、底部固定的全高布局**

  在 `ProfileDetailView.vue` 中将外层结构调整为：

  ```vue
  <div class="flex h-[calc(100vh-3.5rem)] min-h-0 flex-col">
    <header class="shrink-0">返回、名称、标识、描述和状态</header>
    <div class="min-h-0 flex-1 overflow-y-auto pb-4">
      <!-- 接入命令、记忆、服务、文档、代码仓库、高级选项 -->
    </div>
    <div class="shrink-0 border-t border-border bg-card/95 px-4 py-3 backdrop-blur" aria-live="polite">
      <!-- saveError + 取消 + 确认 -->
    </div>
  </div>
  ```

  详情滚动容器内不再使用原 Dialog 的 `max-h-[85vh] overflow-y-auto`；底部按钮在加载中、保存中和错误状态都保持渲染，确认按钮按 `canSaveConfig` 禁用。

- [ ] **Step 6: 先补结构测试，再运行定向测试**

  新建 `frontend/capabilities/tests/profileDetailLayout.test.ts`，读取 `App.vue`、`ProfilesView.vue` 和 `ProfileDetailView.vue` 三个源码文件并断言：

  ```ts
  assert.match(appSource, /<ProfilesView[^>]*:route-key="subRoute"/)
  assert.match(profileSource, /ProfileDetailView/)
  assert.match(profileSource, /requestListNavigation/)
  assert.match(detailSource, /h-\[calc\(100vh-3\.5rem\)\]/)
  assert.match(detailSource, /min-h-0 flex-1 overflow-y-auto/)
  assert.match(detailSource, /放弃未保存修改/)
  assert.match(detailSource, />取消</)
  assert.match(detailSource, />确认</)
  ```

  运行：

  ```bash
  cd frontend/capabilities
  node --import tsx --test tests/profileConfigSnapshot.test.ts tests/profileDetailLayout.test.ts tests/navigation.test.ts
  ```

  预期：全部通过。

- [ ] **Step 7: 提交交互和布局改动**

  ```bash
  git add src/views/capabilities/profileConfigSnapshot.ts src/views/capabilities/ProfileDetailView.vue src/views/capabilities/ProfilesView.vue tests/profileConfigSnapshot.test.ts tests/profileDetailLayout.test.ts
  git commit -m "feat: keep profile detail actions visible"
  ```

### Task 3: 回归验证并交付

**Files:** `frontend/capabilities/src/**`, `frontend/capabilities/tests/**`（仅在验证发现问题时修改）

- [ ] **Step 1: 运行全部前端测试**

  ```bash
  cd frontend/capabilities
  node --import tsx --test tests/*.test.ts
  ```

  预期：现有测试和本次新增测试全部 PASS。

- [ ] **Step 2: 运行类型检查和生产构建**

  ```bash
  npm run typecheck
  npm run build
  ```

  预期：`vue-tsc` 和 Vite 构建均退出码 0，并生成 `src/agent_bridge/static/capabilities` 构建产物。

- [ ] **Step 3: 做一次手动页面验收**

  在 `frontend/capabilities` 启动 `npm run dev`，打开能力平面列表，验证：进入 `#profiles/<key>` 后顶层标题隐藏；滚动详情主体时底部取消/确认持续可见；修改服务或记忆后点击返回、取消和浏览器后退均弹出放弃确认；保存成功回到列表且列表状态刷新。

- [ ] **Step 4: 检查工作区并汇报**

  ```bash
  git status --short
  git log -3 --oneline
  ```

  预期：只包含本次功能相关提交和必要构建验证结果；最终汇报详情页路由、固定操作栏、未保存保护和验证命令结果。
