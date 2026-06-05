<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { ProjectProfile } from '../api/types'

const profiles = ref<ProjectProfile[]>([])
const showAdd = ref(false)
const form = ref({ profile_key: '', name: '', description: '' })

onMounted(async () => { profiles.value = await api.listProfiles() })

async function create() {
  await api.upsertProfile(form.value)
  showAdd.value = false
  profiles.value = await api.listProfiles()
}

async function loadDetail(key: string) {
  const p = await api.getProfile(key)
  const idx = profiles.value.findIndex(x => x.profile_key === key)
  if (idx >= 0) profiles.value[idx] = p
}
</script>

<template>
  <div class="view">
    <div class="view-header">
      <h2>Project Profiles</h2>
      <button @click="showAdd = !showAdd">{{ showAdd ? 'Cancel' : 'New Profile' }}</button>
    </div>

    <form v-if="showAdd" @submit.prevent="create" class="add-form">
      <input v-model="form.profile_key" placeholder="profile_key" required />
      <input v-model="form.name" placeholder="Name" required />
      <input v-model="form.description" placeholder="Description" />
      <button type="submit">Create</button>
    </form>

    <table>
      <thead><tr><th>Key</th><th>Name</th><th>Status</th><th>Rules</th></tr></thead>
      <tbody>
        <tr v-for="p in profiles" :key="p.profile_key" @click="loadDetail(p.profile_key)" style="cursor:pointer">
          <td>{{ p.profile_key }}</td>
          <td>{{ p.name }}</td>
          <td><span :class="['badge', p.status]">{{ p.status }}</span></td>
          <td>{{ p.allow_count ?? 0 }} allow / {{ p.deny_count ?? 0 }} deny</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
