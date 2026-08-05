import type { BusinessLedgerField } from '../api/types'

/** 将 API 中的日期值转换为 HTML 日期输入控件可接受的本地格式。 */
export function businessLedgerInputValue(field: BusinessLedgerField, value: unknown): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  if (field.field_type === 'date') return text.slice(0, 10)
  if (field.field_type === 'datetime') return text.replace(' ', 'T').slice(0, 16)
  return text
}

export function businessLedgerRecordFormValues(
  fields: BusinessLedgerField[],
  values: Record<string, unknown>,
): Record<string, string> {
  return Object.fromEntries(fields.map(field => [field.field_key, businessLedgerInputValue(field, values[field.field_key])]))
}
