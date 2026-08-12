<script setup lang="ts">
import { HelpCircle } from '@lucide/vue'
import { Button } from './ui/button'
import type { ProductTourDefinition } from '../lib/onboardingTours'

defineProps<{ tour: ProductTourDefinition }>()
const emit = defineEmits<{ start: [tour: ProductTourDefinition] }>()
</script>

<template>
  <Button variant="outline" size="lg" @click="emit('start', tour)">
    <HelpCircle :size="14" />
    新手指南
  </Button>
</template>
