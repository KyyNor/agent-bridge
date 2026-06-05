<script setup lang="ts">
export interface NavItem {
  key: string
  label: string
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
  window.location.hash = key
}
</script>

<template>
  <div class="flex min-h-screen">
    <!-- Sidebar -->
    <aside class="fixed top-0 left-0 bottom-0 z-50 flex w-[240px] flex-col border-r border-sidebar-border bg-card">
      <!-- Logo -->
      <div class="border-b border-border/60 px-5 pb-4 pt-5">
        <div class="flex items-center gap-3">
          <div class="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
            AB
          </div>
          <div class="leading-tight">
            <div class="text-[15px] font-semibold text-foreground">Agent Bridge</div>
          </div>
        </div>
      </div>

      <!-- Navigation -->
      <nav class="flex-1 overflow-y-auto px-3 py-3">
        <div v-for="group in navGroups" :key="group.label" class="mb-2">
          <div class="mb-1 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
            {{ group.label }}
          </div>
          <button
            v-for="item in group.items"
            :key="item.key"
            :disabled="item.disabled"
            :class="[
              'flex w-full items-center gap-3 rounded-lg px-3 py-2 mb-0.5 text-[13px] font-medium transition-colors',
              item.disabled
                ? 'cursor-default text-gray-400'
                : active === item.key
                  ? 'bg-accent text-accent-foreground font-semibold'
                  : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
            ]"
            @click="!item.disabled && navigate(item.key)"
          >
            <span>{{ item.label }}</span>
            <span
              v-if="item.badge"
              :class="[
                'ml-auto rounded-full px-1.5 py-px text-[11px] font-semibold',
                active === item.key
                  ? 'bg-primary/15 text-primary'
                  : 'bg-secondary text-muted-foreground'
              ]"
            >{{ item.badge }}</span>
          </button>
        </div>
      </nav>

      <!-- Footer -->
      <div class="border-t border-border/60 px-5 py-4 text-xs text-gray-400">
        {{ footer || 'v0.1.0 · 阶段一' }}
      </div>
    </aside>

    <!-- Main Content -->
    <div class="ml-[240px] flex min-h-screen flex-1 flex-col">
      <slot />
    </div>
  </div>
</template>
