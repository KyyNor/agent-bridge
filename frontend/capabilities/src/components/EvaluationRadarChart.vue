<script setup lang="ts">
import { computed } from 'vue'
import type { ModelEvaluationRadarScore } from '../lib/modelEvaluationRadar'

const props = defineProps<{
  scores: ModelEvaluationRadarScore[]
}>()

const SIZE = 276
const CENTER = SIZE / 2
const RADIUS = 88
const rings = [20, 40, 60, 80, 100]

function coordinates(index: number, percentage = 100, radius = RADIUS) {
  const angle = -Math.PI / 2 + (2 * Math.PI * index) / props.scores.length
  const currentRadius = radius * percentage / 100
  return { x: CENTER + Math.cos(angle) * currentRadius, y: CENTER + Math.sin(angle) * currentRadius }
}

function polygon(percentage: number) {
  return props.scores.map((_, index) => {
    const point = coordinates(index, percentage)
    return `${point.x},${point.y}`
  }).join(' ')
}

const scorePolygon = computed(() => props.scores.map((score, index) => {
  const point = coordinates(index, score.score)
  return `${point.x},${point.y}`
}).join(' '))

const ariaLabel = computed(() => props.scores.map(score => `${score.label} ${score.score.toFixed(1)} 分`).join('，'))
</script>

<template>
  <section class="rounded-md border border-border bg-muted/20 p-4" aria-labelledby="evaluation-radar-title">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h3 id="evaluation-radar-title" class="font-medium">五维能力雷达图</h3>
        <p class="mt-1 text-xs text-muted-foreground">同维度多个已选测试集按百分比分数等权平均；未选维度为 0 分。</p>
      </div>
    </div>
    <div class="mt-3 grid items-center gap-4 sm:grid-cols-[minmax(0,1fr)_210px]">
      <svg class="mx-auto h-auto w-full max-w-[300px]" :viewBox="`0 0 ${SIZE} ${SIZE}`" role="img" :aria-label="ariaLabel">
        <polygon v-for="ring in rings" :key="ring" :points="polygon(ring)" fill="none" stroke="var(--border)" stroke-width="1" />
        <line v-for="(_, index) in scores" :key="index" :x1="CENTER" :y1="CENTER" :x2="coordinates(index).x" :y2="coordinates(index).y" stroke="var(--border)" stroke-width="1" />
        <polygon :points="scorePolygon" fill="var(--chart-1)" fill-opacity="0.18" stroke="var(--chart-1)" stroke-width="2" />
        <circle v-for="(score, index) in scores" :key="score.key" :cx="coordinates(index, score.score).x" :cy="coordinates(index, score.score).y" r="3" fill="var(--chart-1)" />
        <text v-for="(score, index) in scores" :key="score.key" :x="coordinates(index, 100, RADIUS + 24).x" :y="coordinates(index, 100, RADIUS + 24).y" fill="var(--muted-foreground)" font-size="11" text-anchor="middle" dominant-baseline="middle">{{ score.label }}</text>
      </svg>
      <dl class="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <div v-for="score in scores" :key="score.key">
          <dt class="text-xs text-muted-foreground">{{ score.label }}<span v-if="score.selected_datasets" class="ml-1">({{ score.selected_datasets }} 项)</span></dt>
          <dd class="mt-0.5 font-semibold tabular-nums">{{ score.score.toFixed(1) }}</dd>
        </div>
      </dl>
    </div>
  </section>
</template>
