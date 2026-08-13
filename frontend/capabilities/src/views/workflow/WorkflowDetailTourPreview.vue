<script setup lang="ts">
import { FileText, Folder, GitBranch, Play, RotateCw } from '@lucide/vue'
import { Badge } from '../../components/ui/badge'
import { Card, CardContent } from '../../components/ui/card'
import StatusBadge from '../../components/StatusBadge.vue'

defineProps<{ tab: 'overview' | 'tasks' | 'artifacts' | 'runs' | 'versions' }>()

const tasks = [
  { key: '客户回访 / 成都区域', type: '客户分析', status: '待处理', tone: 'blocked' as const, version: 'v3' },
  { key: '产品周报 / 2026-W33', type: '报告生成', status: '增量更新', tone: 'running' as const, version: 'v8' },
  { key: '异常工单 / 高优先级', type: '风险归纳', status: '失败', tone: 'error' as const, version: 'v2' },
]

const artifacts = [
  { title: '经营分析主报告', path: 'reports/经营分析.md', format: 'Markdown', icon: FileText },
  { title: '管理层可视化报告', path: 'reports/经营分析.html', format: 'HTML', icon: FileText },
  { title: '区域明细', path: 'data/区域明细.json', format: 'JSON', icon: Folder },
]

const runs = [
  { id: 'run_20260813_093500', status: '已完成', tone: 'success' as const, started: '今天 09:35', duration: '2m 18s' },
  { id: 'run_20260812_171240', status: '失败', tone: 'error' as const, started: '昨天 17:12', duration: '48s' },
]

const versions = [
  { version: 'v8', source: '页面保存', time: '今天 09:28', note: '调整 Agent 提示词并增加 HTML 输出节点' },
  { version: 'v7', source: 'AI 设计', time: '昨天 16:50', note: '新增风险归纳分支与条件连线' },
  { version: 'v6', source: '导入覆盖', time: '8 月 11 日', note: '更新任务输入字段与超时配置' },
]
</script>

<template>
  <section data-tour="workflow-detail-tour-preview" class="space-y-4 rounded-lg border border-primary/20 bg-card p-4 shadow-card">
    <div class="flex items-center justify-between gap-3 border-b border-border pb-3">
      <div>
        <div class="text-sm font-semibold text-foreground">指南演示 · {{ ({ overview: '概览', tasks: '任务队列', artifacts: '工作流产物', runs: '运行记录', versions: '版本历史' } as const)[tab] }}</div>
        <p class="mt-0.5 text-xs text-muted-foreground">以下为临时示例数据，退出指南后恢复真实页面。</p>
      </div>
      <Badge variant="secondary" class="bg-info-soft text-info-soft-fg">示例数据</Badge>
    </div>

    <template v-if="tab === 'overview'">
      <div class="grid gap-3 md:grid-cols-3">
        <div class="rounded-md border border-border p-3"><div class="text-xs text-muted-foreground">最近运行</div><div class="mt-2 flex items-center gap-2 text-lg font-semibold"><StatusBadge status="success" label="已完成" /><span class="text-sm">2m 18s</span></div><p class="mt-2 text-xs text-muted-foreground">今天 09:35</p></div>
        <div class="rounded-md border border-border p-3"><div class="text-xs text-muted-foreground">待处理任务</div><div class="mt-2 text-2xl font-semibold tabular-nums">3</div><p class="mt-1 text-xs text-muted-foreground">其中 1 条需要重试</p></div>
        <div class="rounded-md border border-border p-3"><div class="text-xs text-muted-foreground">当前产物</div><div class="mt-2 text-2xl font-semibold tabular-nums">12</div><p class="mt-1 text-xs text-muted-foreground">8 个可读报告</p></div>
      </div>
      <div class="grid gap-3 lg:grid-cols-[1.4fr_1fr]">
        <div class="rounded-md border border-border p-3"><div class="mb-3 flex items-center gap-2 text-sm font-semibold"><GitBranch class="h-4 w-4 text-primary" />工作流图</div><div class="flex items-center justify-around rounded-md bg-muted/40 px-4 py-8 text-xs"><span class="rounded border bg-card px-3 py-2">获取任务</span><span>→</span><span class="rounded border border-primary/30 bg-info-soft px-3 py-2">Agent 分析</span><span>→</span><span class="rounded border bg-card px-3 py-2">输出报告</span></div></div>
        <div class="rounded-md border border-border p-3"><div class="text-sm font-semibold">下一步</div><p class="mt-2 text-xs leading-5 text-muted-foreground">先处理失败任务，再检查最新报告是否覆盖本周新增数据。</p><div class="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary"><RotateCw class="h-3.5 w-3.5" />进入任务队列</div></div>
      </div>
    </template>

    <template v-else-if="tab === 'tasks'">
      <div class="overflow-hidden rounded-md border border-border">
        <div class="grid grid-cols-[minmax(0,1.6fr)_1fr_110px_70px] gap-3 border-b bg-muted/30 px-3 py-2 text-xs text-muted-foreground"><span>任务</span><span>类型</span><span>状态</span><span>版本</span></div>
        <div v-for="task in tasks" :key="task.key" class="grid grid-cols-[minmax(0,1.6fr)_1fr_110px_70px] items-center gap-3 border-b border-border/60 px-3 py-3 text-sm last:border-b-0"><span class="truncate font-medium">{{ task.key }}</span><span class="text-muted-foreground">{{ task.type }}</span><StatusBadge :status="task.tone" :label="task.status" /><span class="font-mono text-xs text-muted-foreground">{{ task.version }}</span></div>
      </div>
    </template>

    <template v-else-if="tab === 'artifacts'">
      <div class="grid gap-3 md:grid-cols-3">
        <div v-for="artifact in artifacts" :key="artifact.path" class="rounded-md border border-border p-3"><component :is="artifact.icon" class="h-5 w-5 text-primary" /><div class="mt-3 text-sm font-semibold">{{ artifact.title }}</div><p class="mt-1 truncate font-mono text-xs text-muted-foreground">{{ artifact.path }}</p><Badge variant="outline" class="mt-3">{{ artifact.format }}</Badge></div>
      </div>
    </template>

    <template v-else-if="tab === 'runs'">
      <div class="space-y-2">
        <div v-for="run in runs" :key="run.id" class="grid grid-cols-[minmax(0,1fr)_100px_110px_80px] items-center gap-3 rounded-md border border-border px-3 py-3 text-sm"><div><div class="flex items-center gap-2 font-mono text-xs"><Play class="h-3.5 w-3.5 text-primary" />{{ run.id }}</div></div><StatusBadge :status="run.tone" :label="run.status" /><span class="text-xs text-muted-foreground">{{ run.started }}</span><span class="text-right text-xs tabular-nums">{{ run.duration }}</span></div>
      </div>
    </template>

    <template v-else>
      <div class="space-y-2">
        <div v-for="item in versions" :key="item.version" class="grid grid-cols-[64px_minmax(0,1fr)_100px] gap-3 rounded-md border border-border p-3"><div class="font-mono text-sm font-semibold text-primary">{{ item.version }}</div><div><div class="text-sm font-medium">{{ item.note }}</div><p class="mt-1 text-xs text-muted-foreground">{{ item.source }}</p></div><span class="text-right text-xs text-muted-foreground">{{ item.time }}</span></div>
      </div>
    </template>
  </section>
</template>
