<script setup lang="ts">
import { UserRound } from '@lucide/vue'

defineProps<{
  name: string
  userId: string
  groupName: string | null
}>()
</script>

<template>
  <div
    class="flex items-center gap-2.5 rounded-md bg-muted/50 px-2.5 py-2"
    :title="groupName ? `${userId} · ${groupName}` : `${userId} · 尚未分配小组`"
  >
    <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
      <UserRound :size="13" />
    </div>
    <div class="min-w-0 flex-1">
      <div class="truncate text-xs font-medium text-foreground">{{ name }}</div>
      <div class="truncate text-[10px] text-muted-foreground">{{ groupName || '未分组' }}</div>
    </div>
  </div>
</template>
