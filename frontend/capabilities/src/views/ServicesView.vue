<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { useApi } from '../composables/useApi'
import type { McpService, CatalogSource } from '../api/types'

const { data: catalog, loading, error, load } = useApi<{ sources: CatalogSource[] }>(() => api.catalog())
const services = ref<McpService[]>([])

const showAdd = ref(false)
const form = ref({ service_key: '', name: '', endpoint_url: '', description: '', tags: '' })

onMounted(async () => {
  await load()
  services.value = await api.listServices()
})

async function register() {
  await api.registerService({
    service_key: form.value.service_key,
    name: form.value.name,
    endpoint_url: form.value.endpoint_url,
    description: form.value.description,
    tags: form.value.tags.split(',').map(t => t.trim()).filter(Boolean),
  })
  showAdd.value = false
  services.value = await api.listServices()
  await load()
}

async function toggleStatus(svc: McpService) {
  const newStatus = svc.status === 'enabled' ? 'disabled' : 'enabled'
  await api.updateServiceStatus(svc.service_key, newStatus)
  services.value = await api.listServices()
}

async function syncTools(key: string) {
  await api.syncServiceTools(key)
  services.value = await api.listServices()
}
</script>

<template>
  <div class="view">
    <div class="view-header">
      <h2>MCP Services</h2>
      <button @click="showAdd = !showAdd">{{ showAdd ? 'Cancel' : 'Register Service' }}</button>
    </div>

    <div v-if="loading">Loading...</div>
    <div v-if="error" class="error">{{ error }}</div>

    <form v-if="showAdd" @submit.prevent="register" class="add-form">
      <input v-model="form.service_key" placeholder="service_key" required />
      <input v-model="form.name" placeholder="Name" required />
      <input v-model="form.endpoint_url" placeholder="Endpoint URL" required />
      <input v-model="form.description" placeholder="Description" />
      <input v-model="form.tags" placeholder="Tags (comma separated)" />
      <button type="submit">Register</button>
    </form>

    <table v-if="services.length">
      <thead>
        <tr><th>Key</th><th>Name</th><th>Status</th><th>Actions</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in services" :key="s.service_key">
          <td>{{ s.service_key }}</td>
          <td>{{ s.name }}</td>
          <td><span :class="['badge', s.status]">{{ s.status }}</span></td>
          <td class="actions">
            <button @click="toggleStatus(s)">{{ s.status === 'enabled' ? 'Disable' : 'Enable' }}</button>
            <button @click="syncTools(s.service_key)">Sync Tools</button>
            <a :href="`#tools&service=${s.service_key}`">View Tools</a>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
