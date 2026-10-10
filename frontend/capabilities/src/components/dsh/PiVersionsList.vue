<script setup lang="ts">
/**
 * Pi Agent 依赖版本清单：随 Agent Bridge 发布的托管清单，钉死精确版本，
 * 避免管理员自行升级导致工作台无法启动。
 */
import type { PiVersions } from '../../api/types'

defineProps<{ versions: PiVersions | null }>()
</script>

<template>
  <div class="space-y-1">
    <div class="text-xs text-muted-foreground">
      Pi 依赖版本随 Agent Bridge 一起发布，版本钉死后随安装包分发；升级 Pi 时同步更新本仓库的托管清单。
    </div>
    <div v-if="versions?.node_engine" class="text-xs text-muted-foreground">
      Node 引擎要求：<span class="font-mono">{{ versions.node_engine }}</span>
    </div>
    <div v-if="versions" class="rounded-md border border-border">
      <ul class="divide-y divide-border">
        <li
          v-for="item in versions.packages"
          :key="item"
          class="px-3 py-1.5 font-mono text-xs"
        >
          {{ item }}
        </li>
      </ul>
    </div>
    <div v-else class="text-xs text-muted-foreground">未获取到 Pi 依赖版本清单</div>
  </div>
</template>