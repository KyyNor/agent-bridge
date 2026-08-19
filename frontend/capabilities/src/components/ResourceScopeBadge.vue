<script setup lang="ts">
import { onMounted } from 'vue'
import type { ResourceScopeFields } from '../api/types'
import { useAccessGroups } from '../composables/useAccessGroups'
import { SHARED_RESOURCE_BADGE_CLASS, SHARED_RESOURCE_READ_ONLY_HINT } from '../lib/resourceAccess'
import { Badge } from './ui/badge'

const props = defineProps<{
  resource: ResourceScopeFields | null | undefined
  /** 当前用户无维护权限的共享资源，在徽章下方提示只读。 */
  readOnly?: boolean
}>()

const { ensureLoaded, groupDisplayName } = useAccessGroups()
onMounted(() => { void ensureLoaded() })
</script>

<template>
  <div v-if="resource">
    <template v-if="resource.visibility === 'shared'">
      <Badge variant="secondary" :class="SHARED_RESOURCE_BADGE_CLASS">共享</Badge>
      <div v-if="readOnly" class="mt-1 text-[10px] text-muted-foreground">{{ SHARED_RESOURCE_READ_ONLY_HINT }}</div>
    </template>
    <template v-else>
      <Badge variant="outline">组内</Badge>
      <div class="mt-1 text-[10px] text-muted-foreground" :title="resource.owner_group_key">
        {{ groupDisplayName(resource.owner_group_key) }}
      </div>
    </template>
  </div>
</template>
