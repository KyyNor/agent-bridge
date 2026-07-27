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
