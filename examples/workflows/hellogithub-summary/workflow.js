// workflow.js — GitHub 仓库速览
// 结构定义见同目录 manifest.json；下方 export const manifest 字面量与之保持一致（项目惯例，
// 便于 claude 在运行时读取 schema）。注册时：workflow_js 字段填本文件内容，manifest 字段填 manifest.json。
export const manifest = {
  name: "GitHub 仓库速览",
  description: "从 HelloGitHub 热榜获取 GitHub 仓库，逐个生成结构化速览 markdown（含重写描述分析表）。",
  nodes: [
    { id: "get_task", kind: "io", description: "workflow_get_task 租约一个待处理任务" },
    { id: "seed_tasks", kind: "source", description: "无任务时 curl hotboard?type=hellogithub，抽取 owner/repo，set_task 批量建任务" },
    { id: "fetch_repo", kind: "fetch", description: "curl get-github-repo?repo=owner/repo 取结构化数据" },
    { id: "fetch_readme", kind: "fetch", description: "WebFetch 仓库主页/README 作为事实依据" },
    { id: "web_research", kind: "research", description: "WebSearch 查主要竞品与已知问题" },
    { id: "write_artifact", kind: "output", description: "写 out/artifacts/<owner>-<repo>.md" },
    { id: "emit_result", kind: "io", description: "写 out/result.json" },
  ],
  edges: [
    { from: "get_task", to: "seed_tasks", when: "task == null" },
    { from: "seed_tasks", to: "get_task" },
    { from: "get_task", to: "fetch_repo", when: "task != null" },
    { from: "fetch_repo", to: "fetch_readme" },
    { from: "fetch_readme", to: "web_research" },
    { from: "web_research", to: "write_artifact" },
    { from: "write_artifact", to: "emit_result" },
  ],
  schemas: {
    task: {
      task_key: "owner/repo",
      payload: {
        repo: "owner/repo",
        title: "string",
        url: "string",
        hot_value: "string",
        board_index: "number",
        source: "hellogithub",
      },
    },
    artifact: {
      title: "owner/repo",
      path: "repos/<owner>/<repo>.md",
      file: "out/artifacts/<owner>-<repo>.md",
      tags: ["github", "repo-summary"],
      format: "markdown",
    },
  },
};

/*
 * ============================================================================
 * 执行过程（严格按序）。你是一个在 Agent Bridge 工作流中运行的 agent。
 *
 * 可用工具：
 *   MCP（本工作流注入，无需联网）：
 *     - workflow_get_task()                                          返回 {task:{...}} 或 {task:null}；调用即租约该任务
 *     - workflow_set_task({tasks:[{task_key, payload}, ...]})        幂等创建/刷新任务
 *     - workflow_run_log({level, stage, message, task_key, payload}) 追加运行日志
 *   联网（已授权）：
 *     - Bash：用 curl 调 uapis.cn 的 GET 接口
 *     - WebFetch：抓取仓库主页/README
 *     - WebSearch：调研竞品与已知问题
 *
 * 文件输出：
 *   - 每个 repo 的 markdown 写到 ./out/artifacts/<owner>-<repo>.md（owner/repo 中的 / 用 - 替换）
 *   - 终态写到 ./out/result.json
 *
 * 硬约束（违反则结果会被服务端拒绝）：
 *   - result.json 的 task_key 必须与本次 workflow_get_task 租约到的 task_key 完全一致
 *   - artifact 的 file 路径必须在 run 目录内；path 不得以 / 开头、不得含 ..
 *   - 一次 run 只完成一个 task（result.json 只声明一个 task_key）
 * ============================================================================
 *
 * STEP 1 — 取任务
 *   调用 workflow_get_task。
 *   - 返回 {task: {...}}：记 repo = task.task_key（形如 "owner/repo"），进入 STEP 3。
 *   - 返回 {task: null}：进入 STEP 2。
 *
 * STEP 2 — 补充任务（仅当 task 为 null 时）
 *   workflow_run_log({stage: "seed", message: "拉取 hellogithub 热榜并建任务"})。
 *   用 Bash 执行：curl -sS 'https://uapis.cn/api/v1/misc/hotboard?type=hellogithub'
 *   解析返回 JSON 的 list 数组，对每个 entry：
 *     - 优先从 entry.url 匹配 github.com/<owner>/<repo> 提取 owner/repo；
 *       若 url 不含 github.com，则尝试从 entry.title 中提取 owner/repo。
 *     - 无法提取的条目跳过。
 *     - 生成 {task_key: "<owner>/<repo>", payload: {repo: "<owner>/<repo>", title: entry.title,
 *       url: entry.url, hot_value: entry.hot_value, board_index: entry.index, source: "hellogithub"}}。
 *   调用 workflow_set_task({tasks: [收集到的所有任务]})。
 *     （幂等：状态为 completed 的任务会被 skipped_completed 而不重建——因此重复 seed 安全，
 *      新上榜的 repo 才会被 created。）
 *   再次调用 workflow_get_task。
 *     - 仍为 {task: null}：写 ./out/result.json =
 *         {"status": "no_executable_task", "reason": "no repos extracted from hellogithub board"}
 *       然后结束。
 *     - 否则：记 repo = task.task_key，进入 STEP 3。
 *
 * STEP 3 — 取仓库结构化数据
 *   workflow_run_log({stage: "fetch", task_key: repo, message: "get-github-repo"})。
 *   用 Bash 执行：curl -sS 'https://uapis.cn/api/v1/github/repo?repo=<owner>/<repo>'
 *   解析 JSON，记录：name、description、homepage、default_branch（或 branches）、
 *     language（或 languages）、topics、license、stars、forks、open_issues 等。
 *   硬失败处理：若 HTTP 状态非 200，或返回缺少 name 等核心字段，
 *     不要写 result.json，直接结束本次 run（本次 run 会被判为失败 → 调度器把该任务释放回
 *     pending 重试，超过 3 次则 abandoned，不会阻塞其它 repo）。
 *
 * STEP 4 — 抓 README 作为事实依据
 *   workflow_run_log({stage: "readme", task_key: repo})。
 *   用 WebFetch 抓取 https://github.com/<owner>/<repo>，提炼用于「解决的手段」「核心使用场景」
 *   的事实。软失败：抓取失败不致命，标记 README 不可用并继续 STEP 5（相关格子据已知信息简述）。
 *
 * STEP 5 — Web 调研
 *   workflow_run_log({stage: "research", task_key: repo})。
 *   用 WebSearch 调研该类工具的「主要竞品」与「已知问题/局限」（可结合 open_issues 数量）。
 *
 * STEP 6 — 写产物 markdown
 *   文件名：owner/repo 中的 / 替换为 -，写到 ./out/artifacts/<owner>-<repo>.md。
 *   内容严格遵循下方 TEMPLATE。
 *
 * STEP 7 — 写终态
 *   写 ./out/result.json：
 *   {
 *     "status": "completed",
 *     "task_key": "<repo，即租约到的 task_key>",
 *     "artifacts": [{
 *       "title": "<owner>/<repo>",
 *       "path": "repos/<owner>/<repo>.md",
 *       "file": "out/artifacts/<owner>-<repo>.md",
 *       "tags": ["github", "repo-summary"],
 *       "format": "markdown",
 *       "summary": "<一句话概述，<= 80 字>"
 *     }]
 *   }
 *
 * ----------------------------------------------------------------------------
 * MARKDOWN TEMPLATE（<...> 为占位，按实际数据填写；缺失字段写 "未提供"）
 * ----------------------------------------------------------------------------
 * # <owner>/<repo>
 *
 * > <一句话概述> · ⭐ <stars> · 🍴 <forks> · 📜 <license 或 "无">
 *
 * - **描述**：<description 或 "未提供">
 * - **主页**：<homepage 或 repo 的 github url>
 * - **默认分支 / 分支**：<default_branch 或 分支列表/数量>
 * - **语言**：<language 或 languages>
 * - **话题标签**：<topics，逗号分隔；无则 "无">
 * - **开源协议**：<license 或 "未声明">
 * - **统计**：⭐ <stars> stars · 🍴 <forks> forks · 📋 <open_issues> open issues
 *
 * ## 重写描述
 *
 * | 维度 | 说明 |
 * | --- | --- |
 * | 解决的主要问题 | <它解决了什么痛点> |
 * | 解决的手段 | <技术手段/架构，依据 README> |
 * | 核心使用场景 | <谁、在什么场景下用它，依据 README> |
 * | 主要竞品 | <同类项目，依据 WebSearch；可附简短对比> |
 * | 已知问题 | <局限/常见问题，依据 WebSearch + open_issues> |
 * ============================================================================
 */
