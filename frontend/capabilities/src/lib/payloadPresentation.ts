import { formatJsonValue } from './jsonDisplay'

export type PayloadLanguage = 'markdown' | 'json' | 'html' | 'python' | 'javascript' | 'text'

export interface PayloadHints {
  contentType?: string
  ref?: string
}

export interface PayloadPresentation {
  content: string
  language: PayloadLanguage
}

/** MCP execute 的统一信封中，可单独展示的结构化结果。 */
export interface McpStructuredPayload {
  service: string
  toolName: string
  success: boolean | null
  structured: Record<string, unknown> | unknown[]
}

export function payloadText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function detectPayloadLanguage(content: string, hints: PayloadHints = {}): PayloadLanguage {
  const contentType = (hints.contentType || '').toLowerCase()
  const ref = (hints.ref || '').toLowerCase()
  const text = content.trim()
  if (contentType.includes('markdown') || /\.(md|markdown)$/.test(ref)) return 'markdown'
  if (contentType.includes('json') || /\.json$/.test(ref)) return 'json'
  if (contentType.includes('html') || /\.(html?|xhtml)$/.test(ref) || /^<!doctype\s+html|^<html\b/i.test(text)) return 'html'
  try {
    JSON.parse(text)
    if (text.startsWith('{') || text.startsWith('[')) return 'json'
  } catch {
    // 纯文本或源代码继续按启发式规则识别。
  }
  if (/^\s*(#|>|[-*]\s|\d+\.\s|```)/m.test(text) || /\[[^\]]+\]\([^)]+\)/.test(text)) return 'markdown'
  if (/\b(def|class|from|import)\s+[A-Za-z_]|__name__|print\(/.test(text)) return 'python'
  if (/\b(const|let|var|function|import|export)\b|=>/.test(text)) return 'javascript'
  return 'text'
}

export function payloadLanguageLabel(language: PayloadLanguage): string {
  return {
    markdown: 'Markdown',
    json: 'JSON',
    html: 'HTML',
    python: 'Python',
    javascript: 'JavaScript',
    text: '纯文本',
  }[language]
}

export function preparePayloadPresentation(value: unknown, hints: PayloadHints = {}): PayloadPresentation {
  const raw = payloadText(value)
  const language = detectPayloadLanguage(raw, hints)
  return {
    content: language === 'json' ? formatJsonValue(raw) : raw,
    language,
  }
}

/**
 * 识别 MetaMCP execute 的响应信封。
 *
 * 一些上游 MCP 会把 structuredContent 再编码为 JSON 字符串。这里仅接受完整的
 * JSON 对象或数组，避免把普通文本错误地当成结构化结果；原始响应仍由调用方保留。
 */
export function extractMcpStructuredPayload(content: string): McpStructuredPayload | null {
  let envelope: unknown
  try {
    envelope = JSON.parse(content)
  } catch {
    return null
  }
  if (!isRecord(envelope) || !isRecord(envelope.result)) return null
  if (typeof envelope.service !== 'string') return null

  const toolName = typeof envelope.tool_name === 'string'
    ? envelope.tool_name
    : typeof envelope.tool === 'string' ? envelope.tool : ''
  if (!toolName) return null

  const rawStructured = envelope.result.structured
  let structured: unknown
  if (typeof rawStructured === 'string') {
    try {
      structured = JSON.parse(rawStructured)
    } catch {
      return null
    }
  } else {
    structured = rawStructured
  }
  if (!isJsonContainer(structured)) return null

  return {
    service: envelope.service,
    toolName,
    success: typeof envelope.success === 'boolean' ? envelope.success : null,
    structured,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isJsonContainer(value: unknown): value is Record<string, unknown> | unknown[] {
  return Array.isArray(value) || isRecord(value)
}
