import type { ToolCallLog } from '../api/types'

type MarkdownPreview = { title: string; markdown: string }
type JsonObject = Record<string, unknown>

const HOOK_PREVIEW_TITLES: Record<string, string> = {
  'session-start': '会话启动',
  'session-init': '会话初始化',
  'full-probe': '全量检索探测',
  full_probe: '全量检索探测',
}

function parseJsonObject(value: unknown): JsonObject | null {
  if (typeof value !== 'string') return null
  try {
    const parsed: unknown = JSON.parse(value)
    return isJsonObject(parsed) ? parsed : null
  } catch {
    return null
  }
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function hookAdditionalContext(response: JsonObject): string | null {
  const stdout = nonEmptyString(response.stdout)
  if (!stdout) return null
  const hookResponse = parseJsonObject(stdout)
  if (!hookResponse || !isJsonObject(hookResponse.hookSpecificOutput)) return null
  return nonEmptyString(hookResponse.hookSpecificOutput.additionalContext)
}

function codeGraphMarkdown(response: JsonObject): string | null {
  if (!isJsonObject(response.mcp_result) || !Array.isArray(response.mcp_result.content)) return null
  const textContent = response.mcp_result.content.find(
    item => isJsonObject(item) && item.type === 'text' && typeof item.text === 'string',
  )
  return textContent && isJsonObject(textContent) ? nonEmptyString(textContent.text) : null
}

/**
 * Extracts Markdown from the explicitly supported log payload paths only.
 * Unknown tool responses intentionally remain available only in the JSON viewer.
 */
export function extractLogMarkdownPreview(
  log: Pick<ToolCallLog, 'tool_name' | 'response_json'>,
): MarkdownPreview | null {
  const response = parseJsonObject(log.response_json)
  if (!response || !log.tool_name) return null

  const hookTitle = HOOK_PREVIEW_TITLES[log.tool_name]
  if (hookTitle) {
    const markdown = hookAdditionalContext(response)
    return markdown ? { title: hookTitle, markdown } : null
  }

  if (log.tool_name === 'codegraph_explore') {
    const markdown = codeGraphMarkdown(response)
    return markdown ? { title: 'CodeGraph 代码探索', markdown } : null
  }

  return null
}
