<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, Bot, Check, Clock3, FileTerminal, LoaderCircle, XCircle } from 'lucide-vue-next'
import type { WorkflowGraph, WorkflowNodeRun } from '../../api/types'

const props = defineProps<{ definitionSnapshot: WorkflowGraph; nodeRuns: WorkflowNodeRun[] }>()
const emit = defineEmits<{ openAgentRun: [runKey: string]; openScriptRun: [runId: string] }>()
const runs = computed(() => new Map(props.nodeRuns.map(run => [run.node_id, run])))
function icon(type: string) { return type === 'agent' ? Bot : type === 'script' ? FileTerminal : Clock3 }
function statusClass(status?: string) { return ({ pending: 'border-slate-300 bg-slate-50 text-slate-600', running: 'border-blue-300 bg-blue-50 text-blue-700', completed: 'border-green-300 bg-green-50 text-green-700', skipped: 'border-slate-200 bg-slate-100 text-slate-500', failed: 'border-red-300 bg-red-50 text-red-700', cancelled: 'border-slate-500 bg-slate-100 text-slate-700', warning: 'border-amber-300 bg-amber-50 text-amber-800' } as Record<string, string>)[status || 'pending'] }
function statusIcon(status?: string) { return status === 'completed' ? Check : status === 'failed' ? XCircle : status === 'warning' ? AlertTriangle : status === 'running' ? LoaderCircle : Clock3 }
function open(run: WorkflowNodeRun | undefined) { if (run?.agent_run_key) emit('openAgentRun', run.agent_run_key); else if (run?.script_run_id) emit('openScriptRun', run.script_run_id) }
</script>

<template>
  <section class="space-y-3">
    <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      <button v-for="node in definitionSnapshot.nodes" :key="node.id" type="button" class="min-h-24 border p-3 text-left" :class="statusClass(runs.get(node.id)?.status)" @click="open(runs.get(node.id))">
        <div class="flex items-center gap-2"><component :is="icon(node.type)" class="h-4 w-4" /><span class="min-w-0 flex-1 truncate text-sm font-medium">{{ node.name }}</span><component :is="statusIcon(runs.get(node.id)?.status)" class="h-4 w-4" /></div>
        <div class="mt-2 flex items-center justify-between gap-2 font-mono text-[11px]"><span>{{ node.type }}</span><span>{{ runs.get(node.id)?.status || 'pending' }}</span></div>
        <div v-if="runs.get(node.id)?.error" class="mt-2 line-clamp-2 text-xs">{{ runs.get(node.id)?.error }}</div>
      </button>
    </div>
    <div v-for="edge in definitionSnapshot.edges" :key="edge.id" class="border-l-2 px-3 py-2 text-xs text-muted-foreground">
      <span class="font-mono">{{ edge.source }} → {{ edge.target }}</span>
      <span v-if="runs.get(edge.target)?.condition_results?.find(item => item.edge_id === edge.id)" class="ml-2">{{ runs.get(edge.target)?.condition_results?.find(item => item.edge_id === edge.id)?.matched ? '命中' : '未命中' }} · {{ JSON.stringify(runs.get(edge.target)?.condition_results?.find(item => item.edge_id === edge.id)?.actual) }}</span>
    </div>
  </section>
</template>
