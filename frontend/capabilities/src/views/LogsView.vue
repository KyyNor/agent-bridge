<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { ToolCallLog } from '../api/types'

const logs = ref<ToolCallLog[]>([])
const loading = ref(false)
const filters = ref({ status: '', limit: 50 })

onMounted(() => loadLogs())

async function loadLogs() {
  loading.value = true
  const params: Record<string, string | number> = { limit: filters.value.limit }
  if (filters.value.status) params.status = filters.value.status
  logs.value = await api.listLogs(params)
  loading.value = false
}
</script>

<template>
  <div class="view">
    <h2>Tool Call Logs</h2>
    <div class="filters">
      <select v-model="filters.status" @change="loadLogs">
        <option value="">All Statuses</option>
        <option value="success">Success</option>
        <option value="error">Error</option>
        <option value="denied">Denied</option>
      </select>
      <button @click="loadLogs">Refresh</button>
    </div>

    <div v-if="loading">Loading...</div>

    <table v-if="logs.length">
      <thead><tr><th>Time</th><th>Actor</th><th>Entrypoint</th><th>Tool</th><th>Status</th></tr></thead>
      <tbody>
        <tr v-for="l in logs" :key="l.log_id">
          <td>{{ l.created_at?.slice(0, 19) }}</td>
          <td>{{ l.actor }}</td>
          <td>{{ l.entrypoint }}</td>
          <td>{{ l.tool_name || '-' }}</td>
          <td><span :class="['badge', l.status]">{{ l.status }}</span></td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="!loading">No logs found.</p>
  </div>
</template>
