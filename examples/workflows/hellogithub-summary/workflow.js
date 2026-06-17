export const meta = {
  name: 'hellogithub-summary',
  description: '从 HelloGitHub 热榜取 GitHub 仓库任务，逐个生成结构化速览 markdown（含重写描述分析表）。',
  phases: [
    { title: 'Lease', detail: 'workflow_get_task 租约任务；无则 curl hellogithub 热榜建任务后再租约' },
    { title: 'Enrich', detail: 'curl get-github-repo 取结构化数据，再并行抓 README / Web 调研竞品与已知问题' },
    { title: 'Emit', detail: '写速览 markdown 与 out/result.json' },
  ],
}

/*
 * ============================================================================
 * 本文件是【Claude Code 动态工作流规范】的可执行脚本：harness 的 JS 运行时跑控制流
 * （if / await / parallel），每个 agent() 调用派生一个子 agent 去真正调工具。运行时由
 * claude 以 Workflow({ scriptPath: "./workflow.js" }) 方式执行。
 *
 * 同目录 manifest.json 是 agent-bridge 的【结构定义】（name/nodes/edges/schemas，供平台
 * 注册/渲染），与本可执行脚本分属两套运行时、互不解析——所以这里不再放 export const manifest。
 *
 * 业务流程（一次 run 只完成一个 task）：
 *   workflow_get_task 租约 → 无则拉热榜建任务再租约 → curl get-github-repo →
 *   parallel(抓 README / Web 调研) → 写速览 markdown + out/result.json
 *
 * 外部 API（已实测，均 200、无鉴权 token）：
 *   热榜  GET https://uapis.cn/api/v1/misc/hotboard?type=hellogithub
 *         -> { type, update_time, list:[{ index, title(中文描述，非仓库名),
 *              url(https://github.com/o/r), hot_value(字符串),
 *              extra:{ full_name:"o/r", primary_lang, summary } }] }
 *   仓库  GET https://uapis.cn/api/v1/github/repo?repo=owner/repo
 *         -> 扁平对象: full_name / description / homepage / default_branch(=primary_branch，
 *            无分支列表) / language / languages(对象) / topics(数组，常 []) /
 *            license(全称如 "MIT License"，非 SPDX key) / stargazers(int，非 stars) /
 *            forks(int) / open_issues(int)
 *
 * agent-bridge 服务端硬约束（与脚本无关，运行时强制；违反 → run 判 failed、任务释放重试）：
 *   - result.json 的 task_key 必须等于本次 workflow_get_task 租约值；一次 run 一个 task。
 *   - artifact.file 必须在 ./out/ 之下、不以 / 开头、不含 ..；format 只能 "markdown"。
 *   - result.status 只能 "completed"(需 task_key + 非空 artifacts) 或 "no_executable_task"(带 reason)。
 *   - workflow_get_task 调用即租约(锁到本 run + attempt_count+1，租期 7200s)；workflow_set_task
 *     幂等：completed 跳过、running 未过期保护、pending/过期/abandoned 重置为 pending。
 * ============================================================================
 */

// ---- schemas：约束每个 agent() 的结构化返回 ----
const TASK_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task'],
  properties: {
    task: {
      oneOf: [
        { type: 'null' },
        {
          type: 'object', additionalProperties: false,
          required: ['task_key'],
          properties: {
            task_key: { type: 'string', description: '形如 "owner/repo"' },
            payload: { type: 'object' },
          },
        },
      ],
    },
  },
}
const REPO_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['fetch_failed'],
  properties: {
    fetch_failed: { type: 'boolean', description: 'HTTP 非 200 或缺 full_name 时为 true，调用方据此中止' },
    full_name: { type: 'string' },
    description: { type: 'string' },
    homepage: { type: 'string' },
    default_branch: { type: 'string' },
    language: { type: 'string' },
    languages: { type: 'object' },
    topics: { type: 'array', items: { type: 'string' } },
    license: { type: 'string', description: '全称，如 "MIT License"' },
    stargazers: { type: 'number', description: '注意键名是 stargazers 不是 stars' },
    forks: { type: 'number' },
    open_issues: { type: 'number' },
  },
}
const README_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['available'],
  properties: {
    available: { type: 'boolean' },
    means: { type: 'string', description: '解决的手段（依据 README）' },
    scenarios: { type: 'string', description: '核心使用场景（依据 README）' },
    topics_extra: { type: 'array', items: { type: 'string' }, description: '从 README 补充的 topics' },
  },
}
const RESEARCH_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['competitors', 'known_issues'],
  properties: {
    competitors: { type: 'string', description: '主要竞品；调研不足写「调研不足」' },
    known_issues: { type: 'string', description: '已知问题/局限' },
  },
}
const EMIT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['status', 'task_key', 'artifact_file'],
  properties: {
    status: { type: 'string', enum: ['completed'] },
    task_key: { type: 'string' },
    artifact_file: { type: 'string' },
  },
}

// owner/repo -> 安全的文件名基（artifact.file 不得含 / .. 等）
function fileBase(repo) {
  return String(repo || '').replace('/', '-').replace(/[^A-Za-z0-9._-]+/g, '-')
}

// ============================================================================
// Phase 1 — 取任务
// ============================================================================
phase('Lease')
let leased = await agent(
  '调用 MCP 工具 workflow_get_task()（无入参）。注意：调用即「租约」——会立即把任务锁到本次 run 并 attempt_count+1，租期 7200s。\n' +
  '若返回非 null 的 task：额外调用 workflow_run_log({ stage:"lease", task_key:<该 task_key>, message:"leased task" }) 记一行。\n' +
  '只返回 workflow_get_task 的结果（{ task:{ task_key, payload } } 或 { task:null }），不要做其它事。',
  { label: 'get_task', phase: 'Lease', schema: TASK_SCHEMA },
)

if (!leased.task) {
  // 无可租约任务 → 拉热榜建任务，再租约一个
  leased = await agent(
    '目标：再租约到一个任务。按序执行：\n' +
    '1) Bash 执行: curl -sS \'https://uapis.cn/api/v1/misc/hotboard?type=hellogithub\'\n' +
    '   响应顶层 { type, update_time, list:[...] }，数组字段名是 list（不是 data）。每项字段：\n' +
    '   index(int) / title(中文描述，不是仓库名) / url(https://github.com/o/r) / hot_value(字符串) /\n' +
    '   extra{ full_name:"o/r", primary_lang, summary, updated_at }。\n' +
    '2) owner/repo 提取（逐级降级，title 永远不要当作仓库名）：\n' +
    '   优先 entry.extra.full_name（已是 "owner/repo"）；否则从 entry.url 去掉 "https://github.com/" 前缀\n' +
    '   和末尾 ".git" 取 owner/repo（需恰好含一个 "/"）；仍不合法则跳过该条。\n' +
    '3) 每个合法项生成 { task_key:"o/r", payload:{ repo:"o/r", title, url, hot_value, board_index:index, source:"hellogithub" } }。\n' +
    '4) 调用 MCP workflow_set_task({ tasks:[所有任务] })；把返回的 { created, updated, skipped_completed, skipped_running }\n' +
    '   用 workflow_run_log({ stage:"seed", message:"seeded hellogithub board", payload:<那个计数> }) 记一行。\n' +
    '5) 再次调用 workflow_get_task() 取一个任务（注意这也会消费一次租约 + attempt_count+1）。\n' +
    '返回第 5 步的结果（{ task:{...} } 或 { task:null }）。',
    { label: 'seed_then_lease', phase: 'Lease', schema: TASK_SCHEMA },
  )
}

if (!leased.task) {
  // 榜单无可提取 repo（或全部 completed）→ 合法的非失败终态
  await agent(
    '用 Write 工具写 ./out/result.json，内容严格为：\n' +
    '{"status":"no_executable_task","reason":"no pending repos from hellogithub board"}\n' +
    '只做这一件事。',
    { label: 'emit_no_task', phase: 'Lease' },
  )
  return { status: 'no_executable_task' }
}

const repo = leased.task.task_key                       // "owner/repo"，即本次租约值（result.json 必须用它）
const payload = (leased.task.payload && typeof leased.task.payload === 'object') ? leased.task.payload : {}
const base = fileBase(repo)

// ============================================================================
// Phase 2 — 富化（取结构化数据 + 并行调研）
// ============================================================================
phase('Enrich')
const repoData = await agent(
  `Bash 执行: curl -sS 'https://uapis.cn/api/v1/github/repo?repo=${repo}'\n` +
  '200 直接返回扁平 JSON 对象（非 data 包裹）。按真实键名提取：full_name、description、homepage、\n' +
  'default_branch(与 primary_branch 同值，本接口无分支列表)、language、languages(对象 {语言:字节数})、\n' +
  'topics(数组，实测常为 [])、license(字符串全称如 "MIT License"，非 SPDX key)、\n' +
  'stargazers(int，注意键名是 stargazers 不是 stars)、forks(int)、open_issues(int)。\n' +
  `硬失败：若 HTTP 非 200 或返回缺 full_name，先用 workflow_run_log({ level:"error", stage:"fetch", task_key:"${repo}", message:"get-github-repo failed", payload:{} }) 记录，\n` +
  '再返回 { fetch_failed:true }（不要写 result.json——不写会让服务端判 not found → run failed → 任务释放重试）。',
  { label: 'fetch_repo', phase: 'Enrich', schema: REPO_SCHEMA },
)

if (repoData.fetch_failed) {
  // 不写 result.json：服务端判失败并释放任务重试（attempt<=阈值回 pending，超阈值 abandoned）
  return { status: 'fetch_failed', repo }
}

const branch = repoData.default_branch || 'main'

// 抓 README 与 Web 调研互相独立 → parallel 并行（fan-out）
const [readmeOut, researchOut] = await parallel([
  () => agent(
    `WebFetch https://github.com/${repo}（可补 https://raw.githubusercontent.com/${repo}/${branch}/README.md 作为事实来源）。\n` +
    '提炼用于「解决的手段」「核心使用场景」的事实，并尝试从 README 补全 topics。\n' +
    '软失败：抓不到或内容不可用就 available=false，相关字段留空——不要中断流程。',
    { label: 'fetch_readme', phase: 'Enrich', schema: README_SCHEMA },
  ),
  () => agent(
    `WebSearch 调研这类工具的「主要竞品」与「已知问题/局限」。参考：${repo}，${repoData.language || ''}，\n` +
    `描述：${repoData.description || ''}，open_issues=${repoData.open_issues}。\n` +
    '软失败：无有价值结果就如实写「调研不足」，不要中断。',
    { label: 'web_research', phase: 'Enrich', schema: RESEARCH_SCHEMA },
  ),
])
const readme = readmeOut || { available: false }
const research = researchOut || { competitors: '调研不足', known_issues: '调研不足' }

// ============================================================================
// Phase 3 — 产出（写 markdown + result.json）
// ============================================================================
phase('Emit')

// result.json 里 task_key/path/file 都是确定的，只有 summary 需 agent 生成 → 直接给骨架
const resultSkeleton = JSON.stringify({
  status: 'completed',
  task_key: repo,
  artifacts: [{
    title: repo,
    path: 'repos/' + repo + '.md',
    file: 'out/artifacts/' + base + '.md',
    tags: ['github', 'repo-summary'],
    format: 'markdown',
    summary: '<=80字一句话概述，需你填写>',
  }],
}, null, 2)

const emitted = await agent(
  '你拿到一个 GitHub 仓库的全部素材，负责写两个文件。严格按下方数据与硬约束执行。\n\n' +
  `仓库 task_key（result.json 的 task_key 必须原样用它）：${repo}\n` +
  `结构化数据：${JSON.stringify(repoData)}\n` +
  `README 要点：${JSON.stringify(readme)}\n` +
  `Web 调研：${JSON.stringify(research)}\n` +
  `榜单上下文 payload：${JSON.stringify(payload)}\n\n` +
  `1) 用 Write 写 ./out/artifacts/${base}.md，内容严格遵循模板（<...> 为占位按数据填，缺失/不可靠字段写「未提供」并据实说明）：\n` +
  '   # <owner>/<repo>\n' +
  '   > <一句话概述> · ⭐ <stargazers> · 🍴 <forks> · 📜 <license 或「无」>\n' +
  '   - **描述**：<description 或「未提供」>\n' +
  '   - **主页**：<homepage 或回退 github 主页>\n' +
  '   - **默认分支**：<default_branch；本数据源不提供分支列表，只有默认分支>\n' +
  '   - **语言**：<language；可用 languages 构成补充>\n' +
  '   - **话题标签**：<topics 逗号分隔；常为空，可据 README 补充；无则「无」>\n' +
  '   - **开源协议**：<license 全称 或「未声明」>\n' +
  '   - **统计**：⭐ <stargazers> stars · 🍴 <forks> forks · 📋 <open_issues> open issues\n' +
  '   ## 重写描述\n' +
  '   | 维度 | 说明 |\n' +
  '   | --- | --- |\n' +
  '   | 解决的主要问题 | <它解决了什么痛点> |\n' +
  '   | 解决的手段 | <技术手段/架构，依据 README；README 不可用据已知信息简述并注明> |\n' +
  '   | 核心使用场景 | <谁、在什么场景下用它，依据 README> |\n' +
  '   | 主要竞品 | <同类项目，依据 WebSearch；调研不足注明> |\n' +
  '   | 已知问题 | <局限/常见问题，依据 WebSearch + open_issues> |\n\n' +
  '2) 用 Write 写 ./out/result.json，内容严格为下面这段 JSON（只把 summary 占位替换成真实的一句话概述，其余字节不变，保持合法 JSON）：\n' +
  resultSkeleton + '\n\n' +
  '硬约束（违反会被服务端拒绝、run 判失败）：result.json 的 task_key 必须等于本次租约值 ' +
  `"${repo}"；file 不得以 / 开头、不得含 ..；format 只能 "markdown"；status 只能 "completed"。`,
  { label: 'write_artifact', phase: 'Emit', schema: EMIT_SCHEMA },
)

return emitted
