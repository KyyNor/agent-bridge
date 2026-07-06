# design_html_report

你正在为 Agent Bridge 的“总结类”工作流生成一份面向人类阅读的 HTML 报告。先理解报告定位，再产出 HTML：

1. `report.html`：把本轮 workflow run 产生的 Markdown 产物，转写为一份完整、自包含、可打印的 HTML 文档。

## 报告定位（务必先理解）

HTML 报告是**派生产物**，给人读，不是给机器检索。

- 它总结的是“这次运行做了什么、得出了什么、有什么风险、下一步该做什么”。
- 它不是 dashboard，不需要交互；它不是日志原文，不要把中间过程原样长篇堆入页面。
- 以本轮 Markdown 产物为核心输入；run logs / 子 agent 事件摘要只是辅助证据，需要时摘要化引用。

## 视觉原则：简约、浅色、直观

目标审美是“干净的文档/编辑风”，不是花哨的落地页。颜色是稀缺资源：用近黑正文 + 一个强调色，其余全部是灰阶。

- ✅ 浅色画布：主背景 `#f7f8fa`，内容纸面 `#ffffff`。
- ✅ 正文近黑 `#1f2937`，次要文字 `#667085`，分隔线 `#e6e8ec`。
- ✅ 只用一个强调色 `#2f6fed`（用于标题装饰、链接、关键数字、序号圆点）。语义色仅在必要时用：成功 `#16845f`、告警 `#b56a09`、危险 `#c63b3b`。
- ✅ 正文宽度 `max-width: 960px`，居中，左右留白；行高 `1.6 ~ 1.72`。
- ✅ 字体用系统栈：`-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif`；代码用 `ui-monospace, "SF Mono", Menlo, Consolas, monospace`。
- ✅ 表格用 hairline（1px 边框）分隔，不要深阴影；表头浅灰底 `#f2f4f7`。
- ✅ 信息分层：标题 → 一句话摘要 → 关键结论 → 证据/过程 → 风险与后续建议。

## 铁律

- ✅ 必须输出**完整 HTML 文档**：以 `<!doctype html>` 开头，含 `<html>`、`<head>`（含 `<meta charset="utf-8">` 与 viewport）、`<body>`。
- ✅ CSS 必须**全部内联**（写在 `<head>` 的 `<style>` 里）；不允许外链样式表。
- ✅ **禁止任何 `<script>` 与外链脚本**；默认禁止 JS。
- ✅ 第一版**禁止图片**（不引入 data URL / 外链图片 / 图标字体），降低安全与路径复杂度。需要可视化时用纯 HTML/CSS 表格、列表、数字卡片。
- ✅ 用 `:root` 集中定义颜色/字号变量，主体样式复用变量。
- ❌ 不要用渐变背景、发光、毛玻璃、深阴影、过大的圆角。
- ❌ 不要在标题里用 emoji；不要用“破折号标题”这类 AI 套话排版（如 `洞察 —— 深度分析`）。
- ❌ 不要把原始日志、完整子 agent transcript 原样贴进页面；超过几行的过程信息要摘要化，必要时用 `<details>` 折叠。
- ❌ 不要伪造数据、版本号、署名；只总结真实提供的输入。

## 最小骨架

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{报告标题}}</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --paper: #ffffff;
      --ink: #1f2937;
      --muted: #667085;
      --line: #e6e8ec;
      --blue: #2f6fed;
      --green: #16845f;
      --amber: #b56a09;
      --red: #c63b3b;
      --code: #f3f5f8;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
      line-height: 1.72;
    }
    main { max-width: 960px; margin: 0 auto; padding: 42px 24px 72px; }
    header { border-bottom: 1px solid var(--line); padding-bottom: 22px; margin-bottom: 28px; }
    h1 { font-size: 28px; line-height: 1.25; margin: 0 0 8px; }
    h2 { font-size: 19px; margin: 30px 0 12px; }
    h3 { font-size: 15px; margin: 20px 0 8px; }
    p { margin: 8px 0; }
    .muted { color: var(--muted); }
    a { color: var(--blue); }
    table { width: 100%; border-collapse: collapse; margin: 12px 0 18px; background: var(--paper); border: 1px solid var(--line); }
    th, td { border-bottom: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; font-size: 14px; }
    th { background: #f2f4f7; font-weight: 600; }
    tr:last-child td { border-bottom: 0; }
    code { background: var(--code); border-radius: 4px; padding: 1px 5px; font-size: 0.92em; }
    details { border: 1px solid var(--line); border-radius: 6px; background: var(--paper); padding: 10px 12px; margin: 10px 0; }
    summary { cursor: pointer; color: var(--muted); }
  </style>
</head>
<body>
<main>
  <header>
    <h1>{{报告标题}}</h1>
    <p class="muted">{{一句话摘要：这次运行的目标与结论}}</p>
  </header>

  <h2>关键结论</h2>
  <!-- 3~5 条要点；重要的放最前 -->

  <h2>过程与证据</h2>
  <!-- 摘要化呈现；原始日志用 <details> 折叠 -->

  <h2>风险与后续建议</h2>
  <!-- 风险点 + 可执行的下一步 -->
</main>
</body>
</html>
```

## 交付前自检清单

输出 HTML 前，逐条确认：

- [ ] 以 `<!doctype html>` 开头，含完整的 `<html>/<head>/<body>`。
- [ ] 所有样式内联在 `<style>` 里，无外链 CSS。
- [ ] 无任何 `<script>`、无外链脚本、无 `<iframe>`、无图片。
- [ ] 颜色只用了规定的变量（一个强调色 + 灰阶），没有彩虹配色。
- [ ] 正文宽度受 `max-width: 960px` 约束，行高在 `1.6~1.72`。
- [ ] 表格用 hairline 分隔，没有深阴影。
- [ ] 标题无 emoji、无“破折号标题”套话。
- [ ] 长篇日志/原始 transcript 已摘要化或用 `<details>` 折叠。
- [ ] 内容分层清晰：标题 → 摘要 → 关键结论 → 证据 → 风险/后续。
- [ ] 没有伪造数据或版本号；只总结真实输入。

## 智能体协作方式

如果智能体要生成 HTML 报告，应先读取本技能：

```text
请执行 execute service='built-in' tool_name='load_skill' params={"skill_name":"design_html_report"} 读取技能，
然后参照技能内容与本轮 Markdown 产物，生成完整的 report.html。
```

智能体完成后应检查：

- 是否输出了完整 HTML 文档（doctype + 内联 CSS）？
- 是否禁用了脚本与图片？
- 是否以 Markdown 产物为主，把日志摘要化、折叠？
- 是否只用了规定的浅色配色与一个强调色？
- 是否通过上面的交付前自检清单？
