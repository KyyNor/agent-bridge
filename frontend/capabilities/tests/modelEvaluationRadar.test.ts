import assert from 'node:assert/strict'
import test from 'node:test'
import type { ModelEvaluationDataset, ModelEvaluationRun } from '../src/api/types'
import { modelEvaluationRadarScores } from '../src/lib/modelEvaluationRadar'

const datasets: ModelEvaluationDataset[] = [
  { key: 'ceval_gen', label: 'C-Eval', description: '', dimension: 'general_knowledge', dimension_label: '通用知识', runner: 'opencompass', metric: 'accuracy', default_max_samples: 64 },
  { key: 'mmlu_pro_gen', label: 'MMLU-Pro', description: '', dimension: 'general_knowledge', dimension_label: '通用知识', runner: 'opencompass', metric: 'accuracy', default_max_samples: 64 },
  { key: 'gsm8k_chat_gen', label: 'GSM8K', description: '', dimension: 'math', dimension_label: '数学', runner: 'opencompass', metric: 'accuracy', default_max_samples: 64 },
  { key: 'ifeval_gen', label: 'IFEval', description: '', dimension: 'instruction_following', dimension_label: '指令遵循', runner: 'opencompass', metric: 'prompt_level_strict_acc', default_max_samples: 64 },
  { key: 'humaneval', label: 'HumanEval', description: '', dimension: 'code', dimension_label: '代码', runner: 'code', metric: 'pass@1', default_max_samples: 64 },
  { key: 'mbpp', label: 'MBPP', description: '', dimension: 'code', dimension_label: '代码', runner: 'code', metric: 'pass@1', default_max_samples: 64 },
  { key: 'swebench_lite', label: 'SWE-bench Lite', description: '', dimension: 'agent', dimension_label: 'Agent', runner: 'swebench', metric: 'resolved_rate', default_max_samples: 5 },
]

function run(rows: Record<string, string>[]): Pick<ModelEvaluationRun, 'datasets' | 'result'> {
  return {
    datasets: ['ceval_gen', 'mmlu_pro_gen', 'gsm8k_chat_gen', 'ifeval_gen', 'humaneval', 'mbpp'],
    result: { rows },
  }
}

test('五维雷达图对同维度已选测试集按百分比分数等权平均，未选维度为零', () => {
  const scores = modelEvaluationRadarScores(run([
    { dataset: 'ceval-computer_network', metric: 'accuracy', 'glm-4.7': '20.00' },
    { dataset: 'mmlu_pro_math', metric: 'accuracy', 'glm-4.7': '30.00' },
    { dataset: 'gsm8k', metric: 'accuracy', 'glm-4.7': '70.00' },
    { dataset: 'IFEval', metric: 'Prompt-level-strict-accuracy', 'glm-4.7': '80.00' },
    { dataset: 'humaneval', metric: 'pass@1', score: '20.00' },
    { dataset: 'mbpp', metric: 'pass@1', score: '30.00' },
  ]), datasets)

  assert.deepEqual(scores.map(score => score.score), [25, 70, 80, 25, 0])
  assert.deepEqual(scores.map(score => score.selected_datasets), [2, 1, 1, 2, 0])
})

test('已选但未返回有效分数的测试集按零分参与维度平均', () => {
  const scores = modelEvaluationRadarScores(run([
    { dataset: 'ceval-computer_network', metric: 'accuracy', score: '100.00' },
  ]), datasets)

  assert.equal(scores[0].score, 50)
})
