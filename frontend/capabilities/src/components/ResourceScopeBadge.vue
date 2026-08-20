<script setup lang="ts">
import type { ResourceScopeFields } from '../api/types'
import GroupBadge from './GroupBadge.vue'
import { SHARED_RESOURCE_BADGE_CLASS, SHARED_RESOURCE_READ_ONLY_HINT } from '../lib/resourceAccess'
import { Badge } from './ui/badge'

defineProps<{
  resource: ResourceScopeFields | null | undefined
  /** 当前用户无维护权限的共享资源，在徽章下方提示只读。 */
  readOnly?: boolean
}>()
</script>

<template>
  <div v-if="resource">
    <div class="flex flex-wrap items-center gap-1.5">
      <GroupBadge :group-key="resource.owner_group_key" />
      <Badge v-if="resource.visibility === 'shared'" variant="secondary" :class="SHARED_RESOURCE_BADGE_CLASS">共享</Badge>
      <Badge v-else variant="outline">组内</Badge>
    </div>
    <div v-if="readOnly" class="mt-1 text-[10px] text-muted-foreground">{{ SHARED_RESOURCE_READ_ONLY_HINT }}</div>
  </div>
</template>
