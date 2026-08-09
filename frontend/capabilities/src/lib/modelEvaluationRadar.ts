import type { ModelEvaluationDataset, ModelEvaluationRun } from '../api/types'

export type ModelEvaluationDimension = ModelEvaluationDataset['dimension']

export interface ModelEvaluationRadarScore {
  key: ModelEvaluationDimension
  label: string
  score: number
  selected_datasets: number
}

const DIMENSIONS: Array<{ key: ModelEvaluationDimension, label: string }> = [
  { key: 'general_knowledge', label: '通用知识' },
  { key: 'math', label: '数学' },
  { key: 'instruction_following', label: '指令遵循' },
  { key: 'code', label: '代码' },
  { key: 'agent', label: 'Agent' },
]

const META_KEYS = new Set(['dataset', 'version', 'metric', 'mode'])

function normalized(value: unknown): string {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '')
}

function rowScore(row: Record<string, string>): number | null {
  const raw = Object.entries(row).find(([key]) => !META_KEYS.has(key))?.[1]
  if (raw == null) return null
  const score = Number.parseFloat(raw.replace('%', ''))
  return Number.isFinite(score) ? Math.max(0, Math.min(100, score)) : null
}

function matchesDataset(datasetKey: string, row: Record<string, string>): boolean {
  const dataset = normalized(row.dataset)
  const metric = normalized(row.metric)
  if (datasetKey === 'ceval_gen') return dataset.startsWith('ceval')
  if (datasetKey === 'mmlu_pro_gen') return dataset.startsWith('mmlupro')
  if (datasetKey === 'gsm8k_chat_gen') return dataset === 'gsm8k'
  if (datasetKey === 'ifeval_gen') return dataset === 'ifeval' && metric === 'promptlevelstrictaccuracy'
  if (datasetKey === 'humaneval') return dataset === 'humaneval'
  if (datasetKey === 'mbpp') return dataset === 'mbpp'
  if (datasetKey === 'swebench_lite') return dataset.includes('swebench')
  return false
}

function datasetScore(datasetKey: string, rows: Record<string, string>[]): number {
  const score = rows.find(row => matchesDataset(datasetKey, row))
  return score ? rowScore(score) ?? 0 : 0
}

/**
 * 生成固定五维雷达图数据：每个已选测试集等权，未选维度为 0 分。
 * 已选测试集没有有效评分时按 0 计，避免部分失败时抬高维度分数。
 */
export function modelEvaluationRadarScores(
  run: Pick<ModelEvaluationRun, 'datasets' | 'result'>,
  datasets: ModelEvaluationDataset[],
): ModelEvaluationRadarScore[] {
  const rows = run.result.rows || []
  return DIMENSIONS.map(dimension => {
    const selected = datasets.filter(dataset => dataset.dimension === dimension.key && run.datasets.includes(dataset.key))
    const score = selected.length
      ? selected.reduce((sum, dataset) => sum + datasetScore(dataset.key, rows), 0) / selected.length
      : 0
    return { key: dimension.key, label: dimension.label, score, selected_datasets: selected.length }
  })
}
