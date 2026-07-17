<script setup lang="ts">
import type { HTMLAttributes } from "vue"
import { cn } from "@/lib/utils"

const props = defineProps<{
  class?: HTMLAttributes["class"]
}>()
</script>

<template>
  <tr
    data-slot="table-row"
    :class="cn('list-row-interactive data-[state=selected]:bg-muted border-b has-aria-expanded:bg-muted/70', props.class)"
  >
    <slot />
  </tr>
</template>
