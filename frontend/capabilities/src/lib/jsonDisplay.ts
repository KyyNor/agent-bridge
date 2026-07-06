export type JsonTokenType =
  | 'whitespace'
  | 'punctuation'
  | 'key'
  | 'string'
  | 'number'
  | 'boolean'
  | 'null'
  | 'plain'

export interface JsonToken {
  type: JsonTokenType
  text: string
}

export function formatJsonValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function tokenizeJson(text: string): JsonToken[] {
  const tokens: JsonToken[] = []
  const pattern = /("(?:\\.|[^"\\])*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(true|false)|(null)|([{}\[\]:,])|(\s+)|([^\s{}\[\]:,]+)/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    const value = match[0]
    if (match[6]) {
      tokens.push({ type: 'whitespace', text: value })
    } else if (match[5]) {
      tokens.push({ type: 'punctuation', text: value })
    } else if (match[1]) {
      const rest = text.slice(pattern.lastIndex)
      tokens.push({ type: /^\s*:/.test(rest) ? 'key' : 'string', text: value })
    } else if (match[2]) {
      tokens.push({ type: 'number', text: value })
    } else if (match[3]) {
      tokens.push({ type: 'boolean', text: value })
    } else if (match[4]) {
      tokens.push({ type: 'null', text: value })
    } else {
      tokens.push({ type: 'plain', text: value })
    }
  }
  return tokens
}
