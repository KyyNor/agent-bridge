import type { WorkflowNodeType } from '../../api/types'

type WorkflowNodeTone = 'blue' | 'teal' | 'violet' | 'amber'
export type WorkflowNodeTonePart = 'rail' | 'badge'

export const workflowNodeToneByType: Record<WorkflowNodeType, WorkflowNodeTone> = {
  get_task: 'blue',
  agent: 'violet',
  script: 'teal',
  output: 'amber',
}

const workflowNodeTypeLabels: Record<WorkflowNodeType, string> = {
  get_task: '获取任务',
  agent: 'Agent',
  script: '托管脚本',
  output: '输出结果',
}

const workflowNodeToneClasses: Record<WorkflowNodeTone, Record<WorkflowNodeTonePart, string>> = {
  blue: { rail: 'bg-cat-blue-fg', badge: 'bg-cat-blue text-cat-blue-fg' },
  teal: { rail: 'bg-cat-teal-fg', badge: 'bg-cat-teal text-cat-teal-fg' },
  violet: { rail: 'bg-cat-violet-fg', badge: 'bg-cat-violet text-cat-violet-fg' },
  amber: { rail: 'bg-cat-amber-fg', badge: 'bg-cat-amber text-cat-amber-fg' },
}

export function workflowNodeToneClass(type: unknown, part: WorkflowNodeTonePart): string {
  const tone = workflowNodeToneByType[type as WorkflowNodeType] || 'blue'
  return workflowNodeToneClasses[tone][part]
}

export function workflowNodeTypeText(type: unknown): string {
  return workflowNodeTypeLabels[type as WorkflowNodeType] ?? String(type ?? '')
}
