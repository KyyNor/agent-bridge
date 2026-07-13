import type { WorkflowReferenceItem } from './workflowReferences'

export const WORKFLOW_VALUE_TYPES = ['string', 'number', 'integer', 'boolean', 'object', 'array'] as const
export type WorkflowValueType = (typeof WORKFLOW_VALUE_TYPES)[number]

const exactReferencePattern = /^\{\{\s*(?:input|task|nodes)\.[A-Za-z0-9_.-]+\s*\}\}$/

export function normalizeWorkflowValueType(value: unknown): WorkflowValueType {
  return typeof value === 'string' && (WORKFLOW_VALUE_TYPES as readonly string[]).includes(value)
    ? value as WorkflowValueType
    : 'string'
}

export function workflowValueType(schema: unknown): WorkflowValueType {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return 'string'
  return normalizeWorkflowValueType((schema as Record<string, unknown>).type)
}

export function workflowValueTypeForReference(
  items: WorkflowReferenceItem[],
  path: string,
  fallbackValue?: unknown,
): WorkflowValueType {
  const matched = items.find(item => item.path === path)
  if (matched) return normalizeWorkflowValueType(matched.type)
  if (typeof fallbackValue === 'number') return Number.isInteger(fallbackValue) ? 'integer' : 'number'
  if (typeof fallbackValue === 'boolean') return 'boolean'
  if (Array.isArray(fallbackValue)) return 'array'
  if (fallbackValue && typeof fallbackValue === 'object') return 'object'
  return 'string'
}

export function defaultWorkflowValue(type: WorkflowValueType): unknown {
  if (type === 'number' || type === 'integer') return 0
  if (type === 'boolean') return false
  if (type === 'object') return {}
  if (type === 'array') return []
  return ''
}

export function formatWorkflowValue(value: unknown, type: WorkflowValueType): string {
  if (typeof value === 'string' && exactReferencePattern.test(value.trim())) return value
  if (type === 'object' || type === 'array') {
    try {
      return JSON.stringify(value ?? defaultWorkflowValue(type), null, 2)
    } catch {
      return type === 'array' ? '[]' : '{}'
    }
  }
  if (value === null || value === undefined) return ''
  return String(value)
}

export function parseWorkflowValue(
  text: string,
  type: WorkflowValueType,
): { ok: true; value: unknown } | { ok: false; message: string } {
  const trimmed = text.trim()
  if (exactReferencePattern.test(trimmed)) return { ok: true, value: trimmed }
  if (type === 'string') return { ok: true, value: text }
  if (type === 'integer') {
    const value = Number(trimmed)
    return trimmed && Number.isInteger(value)
      ? { ok: true, value }
      : { ok: false, message: '请输入整数' }
  }
  if (type === 'number') {
    const value = Number(trimmed)
    return trimmed && Number.isFinite(value)
      ? { ok: true, value }
      : { ok: false, message: '请输入数字' }
  }
  if (type === 'boolean') {
    if (trimmed === 'true') return { ok: true, value: true }
    if (trimmed === 'false') return { ok: true, value: false }
    return { ok: false, message: '布尔值只能是 true 或 false' }
  }
  try {
    const value = JSON.parse(trimmed)
    if (type === 'array' && !Array.isArray(value)) return { ok: false, message: '请输入 JSON 数组' }
    if (type === 'object' && (!value || typeof value !== 'object' || Array.isArray(value))) {
      return { ok: false, message: '请输入 JSON 对象' }
    }
    return { ok: true, value }
  } catch {
    return { ok: false, message: type === 'array' ? '请输入合法 JSON 数组' : '请输入合法 JSON 对象' }
  }
}
