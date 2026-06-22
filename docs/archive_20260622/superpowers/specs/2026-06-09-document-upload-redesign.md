# 文档知识上传交互重设计

## 概述

将「文档知识」页面的文档上传从原生 `<input type="file">` 替换为现代化拖拽上传区域，支持多文件和文件夹上传，移除「稍后同步」选项，在知识处理配置中新增知识同步定时器。

## 当前问题

1. 上传区域使用原生浏览器 file input，"选择文件"按钮不像可交互元素
2. 不支持文件夹上传
3. 「稍后同步」复选框增加不必要的选择负担——实际使用中用户总是选择稍后同步
4. 文档同步缺少独立的定时任务调度

## 设计决策

### 1. 上传入口：知识库列表每行独立上传按钮

在知识库表格每行操作区增加「上传」主按钮，点击弹出上传对话框，目标知识库自动锁定为当前行。

**理由**：方案 A（每行按钮）比方案 B（顶部统一入口）减少一步操作（无需额外选择知识库），适合频繁上传场景。

### 2. 上传对话框：拖拽 + 文件/文件夹选择

对话框内容：
- 顶部显示锁定的目标知识库名称
- 中央拖拽区域（虚线边框），文案提示"拖拽文件或文件夹到此处"
- 两个操作按钮：「选择文件」「选择文件夹」
- 文件选择后显示文件列表（名称 + 大小），可清除或继续添加
- 底部「取消」和「上传 (N)」按钮

### 3. 移除「稍后同步」，永远异步同步

- 删除 `uploadLater` 状态和复选框 UI
- 所有上传走 `addDocument` API 时 `uploadLater` 固定为 `true`
- 上传成功提示文案："上传成功！N 个文件已添加到「<知识库名>」，将在下次同步时处理"

### 4. 知识处理配置新增知识同步定时器

在 `KnowledgeProcessingConfigView.vue` 的「定时任务管理」卡片中新增第三行：
- 标签：知识同步（文档知识同步）
- 默认 cron：`*/30 * * * *`
- 调度状态区域同步新增知识同步的运行状态

## 涉及文件

| 文件 | 变更 |
|------|------|
| `frontend/capabilities/src/views/KnowledgeView.vue` | 移除旧上传 UI、`uploadLater` 状态；新增每行「上传」按钮和上传对话框组件 |
| `frontend/capabilities/src/views/KnowledgeProcessingConfigView.vue` | 新增知识同步 cron 输入行和调度状态展示 |
| `frontend/capabilities/src/api/types.ts` | `KnowledgeSyncConfig` 新增 `doc_sync_cron`；`SchedulerStatus` 新增 `doc_sync` |
| `frontend/capabilities/src/api/client.ts` | 确认 `addDocument` API 签名兼容（uploadLater 已有默认值） |

## 新增/复用组件

- **复用**：`Button`、`Dialog`、`Input`、`Card`、`Badge`（已有）
- **内联实现**：拖拽上传区域直接写在 KnowledgeView.vue 中，使用浏览器原生 Drag & Drop API + `<input webkitdirectory>` 支持文件夹选择，无需引入第三方库

## 数据流

```
用户点击「上传」→ 打开上传对话框（目标 KB 已锁定）
  → 拖拽/选择文件 → 文件列表显示
  → 点击「上传 (N)」→ 循环调用 api.addDocument(file, [kbSlug], true)
  → 成功：Toast 提示 + 刷新文档列表 + 关闭对话框
  → 失败：Toast 错误提示
```

## 后端依赖

- `KnowledgeSyncConfig` 类型需新增 `doc_sync_cron: string` 字段
- `SchedulerStatus` 类型需新增 `doc_sync: SingleSchedulerStatus` 字段
- `GET/PUT /api/sync-config` 需支持 `doc_sync_cron` 的读写
- `GET /api/scheduler-status` 需返回 `doc_sync` 状态
- 后端调度器需新增文档同步定时任务，使用 `doc_sync_cron` 配置

## 边界情况

- 文件类型校验：仅允许 `.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md`
- 空文件（0 字节）：允许上传，由后端校验
- 超大文件：不做前端限制，由 HTTP/server 超时自然限制
- 文件夹上传：递归提取所有符合条件的文件
- 上传中途关闭对话框：不中断已发起的请求
