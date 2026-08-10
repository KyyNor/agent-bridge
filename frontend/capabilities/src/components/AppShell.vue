<script setup lang="ts">
import NavIcon from './NavIcon.vue'
import { navigateTo } from '../lib/navigation'

export interface NavItem {
  key: string
  label: string
  description?: string
  badge?: string | number
  disabled?: boolean
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

defineProps<{
  navGroups: NavGroup[]
  active: string
  footer?: string
}>()

function navigate(key: string) {
  void navigateTo(key)
}
</script>

<template>
  <div class="flex h-screen min-h-0 overflow-hidden">
    <!-- Sidebar -->
    <aside class="fixed top-0 left-0 bottom-0 z-50 flex w-[210px] flex-col bg-card">
      <!-- Logo -->
      <div class="px-5 pb-5 pt-6">
        <div class="flex items-center gap-2.5">
          <div class="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
            智
          </div>
          <span class="text-sm font-semibold text-foreground tracking-tight">智能中枢</span>
        </div>
      </div>

      <!-- Navigation -->
      <nav class="flex-1 overflow-y-auto px-3">
        <div v-for="group in navGroups" :key="group.label" class="mb-4">
          <div class="px-2 pb-1 text-[11px] font-medium text-muted-foreground/50">
            {{ group.label }}
          </div>
          <div class="space-y-0.5">
            <button
              v-for="item in group.items"
              :key="item.key"
              :disabled="item.disabled"
              :class="[
                'flex w-full items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13px] transition-colors',
                item.disabled
                  ? 'cursor-default text-muted-foreground/30'
                  : active === item.key
                    ? 'bg-primary/8 text-primary font-medium'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              ]"
              @click="!item.disabled && navigate(item.key)"
            >
              <NavIcon :name="item.key" />
              <span>{{ item.label }}</span>
              <span
                v-if="item.badge"
                :class="[
                  'ml-auto rounded-full px-1.5 py-px text-[10px] font-semibold',
                  active === item.key
                    ? 'bg-primary/10 text-primary'
                    : 'bg-muted text-muted-foreground'
                ]"
              >{{ item.badge }}</span>
            </button>
          </div>
        </div>
      </nav>

      <!-- Footer -->
      <div class="space-y-3 px-5 py-4">
        <slot name="footer" />
        <div class="text-[11px] text-muted-foreground/40">{{ footer || 'v0.1.0' }}</div>
      </div>
    </aside>

    <!-- Main Content -->
    <div class="ml-[210px] flex h-screen min-h-0 min-w-0 flex-1 flex-col">
      <slot />
    </div>
  </div>
</template>
