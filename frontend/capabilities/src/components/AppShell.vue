<script setup lang="ts">
defineProps<{ navItems: { key: string; label: string }[]; active: string }>()

function navigate(key: string) {
  window.location.hash = key
}
</script>

<template>
  <header class="app-header">
    <h1>Agent Bridge</h1>
    <nav>
      <button
        v-for="item in navItems"
        :key="item.key"
        :class="{ active: active === item.key }"
        @click="navigate(item.key)"
      >{{ item.label }}</button>
    </nav>
  </header>
  <main class="app-main">
    <slot />
  </main>
</template>
