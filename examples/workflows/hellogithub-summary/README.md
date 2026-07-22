# HelloGitHub 仓库速览

`workflow.json` 使用当前 Agent Bridge 结构化 DAG 导入格式，可直接在工作流页面导入。

## 运行方式

工作流一次领取并处理一个预先导入的仓库任务：

1. `get_task` 领取任务；没有待处理任务时以 `no_task` 正常结束。
2. 第一个 Agent 核验仓库主页、README、许可证和活跃度等事实。
3. 第二个 Agent 基于已核验事实调研使用场景、替代方案和限制。
4. 系统输出节点依次生成 Markdown 主报告和 HTML 派生报告。

旧版 `manifest.json + workflow.js` 动态运行时已删除。任务准备、依赖关系、输出格式和 Agent 配置现在全部由受校验的 DAG 节点与边表达。

## 任务输入

运行前通过任务导入功能写入任务。建议 payload 至少包含：

```json
{
  "full_name": "owner/repository",
  "repo_slug": "owner-repository",
  "url": "https://github.com/owner/repository",
  "title": "热榜中文标题",
  "hot_value": "热度文本",
  "source": "hellogithub"
}
```

`repo_slug` 用于产物路径，必须是不含斜杠和 `..` 的安全文件名。

## 环境依赖

- 系统中存在 `github-research` 能力平面，并允许 Agent 使用所需的网页/检索能力。
- Agent backend 使用 `claude`；如项目改用其他 backend，应同时修改 workflow 中所有 `backend_key`。
- HTML 输出使用内置 `design_html_report` skill。
