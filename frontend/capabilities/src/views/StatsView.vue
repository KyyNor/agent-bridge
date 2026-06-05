<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'

const stats = ref<Record<string, unknown>[]>([])
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  const r = await api.stats({ dimensions: 'profile_key,source_key,tool_name' })
  stats.value = (r as unknown as { buckets: Record<string, unknown>[] }).buckets || []
  loading.value = false
})
</script>

<template>
  <div class="view">
    <h2>Tool Call Statistics</h2>
    <div v-if="loading">Loading...</div>
    <table v-if="stats.length">
      <thead><tr><th>Profile</th><th>Source</th><th>Tool</th><th>Count</th></tr></thead>
      <tbody>
        <tr v-for="(s, i) in stats" :key="i">
          <td>{{ s.profile_key || '-' }}</td>
          <td>{{ s.source_key || '-' }}</td>
          <td>{{ s.tool_name || '-' }}</td>
          <td>{{ s.count }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="!loading">No stats available.</p>
  </div>
</template>
