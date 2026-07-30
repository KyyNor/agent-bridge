/** 将工作流条件的实际值转换为适合边标签的紧凑文本。 */
export function formatWorkflowConditionActual(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  if (typeof value !== 'object') return String(value)

  try {
    return JSON.stringify(value)
  } catch {
    return '（无法序列化的对象）'
  }
}
