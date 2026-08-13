<script setup lang="ts">
import { CircleHelp } from '@lucide/vue'
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useOnboardingTour } from '../composables/useOnboardingTour'
import { onboardingTourForRoute } from '../lib/onboardingTours'
import { Button } from './ui/button'

const route = useRoute()
const { startTour, stopTour } = useOnboardingTour()
const tour = computed(() => onboardingTourForRoute(route.name, route.params.routeKey))
const hint = computed(() => tour.value ? `打开${tour.value.name}` : '当前页面暂未配置指南')

watch(() => route.fullPath, stopTour)

function openTour() {
  if (tour.value) void startTour(tour.value)
}
</script>

<template>
  <Button
    type="button"
    variant="outline"
    size="sm"
    class="h-8 w-full justify-start gap-2 text-xs"
    :disabled="!tour"
    :title="hint"
    :aria-label="hint"
    @click="openTour"
  >
    <CircleHelp :size="14" />
    当前页面指南
  </Button>
</template>
