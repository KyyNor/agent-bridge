# 移除 profile use 的绝对 Profile 路径

## 背景

`profile use` 当前会刷新服务端 Profile Markdown 文件，并在 `CLAUDE.md` 的
Agent Bridge 托管块中写入该文件的绝对 `@` 引用。该通道原本用于兜底
`SessionStart` 注入失效。

LiteLLM callback 已解决注入兼容问题，Profile 与 Memory 上下文统一由
`SessionStart` 的 `additionalContext` 注入，因此 `CLAUDE.md` 不再需要重复引用
Profile 文件。

## 变更

- `profile use` 写入的 Agent Bridge 托管块只保留：

```md
`<system-reminder>` 是补充的系统信息。
```

- 再次执行 `profile use` 时，现有托管块中的旧绝对 `@` 路径会被上述说明替换。
- `profile use` 不再调用 `refresh_profile_doc_context_file`。
- 保留 Profile 文件刷新 API、服务端实现及相关领域测试。
- 保留 Agent Runtime 的 `install_profile_to_cwd` 文件安装与 `@` 引用逻辑；该通道
  属于隔离运行时，不在本次 `profile use` 调整范围内。

## 验收

- project 和 user scope 的 `CLAUDE.md` 托管块均不包含绝对 `@` 路径。
- 托管块包含 `<system-reminder>` 语义说明。
- 旧托管块会被幂等替换，文件中的其他用户内容保持不变。
- `profile use` 在 Profile 文件刷新 API不可用时也不受影响，因为它不再调用该
  API。

