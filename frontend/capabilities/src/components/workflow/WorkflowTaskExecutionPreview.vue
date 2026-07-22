<script setup lang="ts">
import type { WorkflowExecutionPlan } from '../../api/types'
import { Badge } from '../ui/badge'

defineProps<{ plan?: WorkflowExecutionPlan }>()
</script>

<template>
  <div v-if="plan" class="border-t bg-background px-3 py-3">
    <div class="mb-2 flex flex-wrap items-center gap-2 text-xs">
      <span class="font-semibold text-foreground">执行预览</span>
      <Badge variant="outline">{{ plan.mode }}</Badge>
      <span class="text-muted-foreground">baseline: {{ plan.baseline_run_id || '无可复用运行' }}</span>
    </div>
    <div class="mb-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
      <span>复用节点 {{ plan.reusable_node_ids.length }}</span>
      <span>重新执行节点 {{ plan.affected_node_ids.length }}</span>
    </div>
    <div class="flex flex-wrap gap-1.5">
      <Badge v-for="node in plan.nodes" :key="node.node_id" variant="outline">
        {{ node.node_id }} · {{ node.action === 'reuse' ? '复用' : '执行' }} · {{ node.reason }}
      </Badge>
    </div>
  </div>
</template>
