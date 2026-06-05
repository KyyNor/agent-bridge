<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { McpTool } from '../api/types'

const tools = ref<McpTool[]>([])
const services = ref<{ service_key: string; name: string }[]>([])
const selectedService = ref('')
const loading = ref(false)

onMounted(async () => {
  services.value = await api.listServices()
})

async function loadTools() {
  if (!selectedService.value) return
  loading.value = true
  tools.value = await api.listTools(selectedService.value)
  loading.value = false
}

async function updateType(svc: string, tool: string, t: string) {
  await api.updateToolType(svc, tool, t)
  await loadTools()
}
</script>

<template>
  <div class="view">
    <h2>Tools</h2>
    <select v-model="selectedService" @change="loadTools">
      <option value="">-- Select Service --</option>
      <option v-for="s in services" :key="s.service_key" :value="s.service_key">{{ s.name }}</option>
    </select>

    <div v-if="loading">Loading...</div>

    <table v-if="tools.length">
      <thead>
        <tr><th>Tool</th><th>Display Name</th><th>Type</th><th>Description</th><th>Actions</th></tr>
      </thead>
      <tbody>
        <tr v-for="t in tools" :key="t.tool_name">
          <td>{{ t.tool_name }}</td>
          <td>{{ t.display_name }}</td>
          <td><span :class="['badge', t.tool_type]">{{ t.tool_type }}</span></td>
          <td class="desc">{{ t.description }}</td>
          <td class="actions">
            <select :value="t.tool_type" @change="updateType(selectedService, t.tool_name, ($event.target as HTMLSelectElement).value)">
              <option value="unconfigured">unconfigured</option>
              <option value="read">read</option>
              <option value="write">write</option>
              <option value="read_write">read_write</option>
            </select>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
