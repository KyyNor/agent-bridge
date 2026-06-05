<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import type { CodeRepository, KnowledgeBaseSummary } from '../api/types'

const repos = ref<CodeRepository[]>([])
const kbs = ref<KnowledgeBaseSummary[]>([])

onMounted(async () => {
  repos.value = await api.listCodeRepos()
  kbs.value = await api.listWikiKbs()
})

async function syncRepo(key: string) {
  await api.syncCodeRepo(key)
  repos.value = await api.listCodeRepos()
}
</script>

<template>
  <div class="view">
    <h2>Built-in CodeGraph Repositories</h2>
    <table v-if="repos.length">
      <thead><tr><th>Key</th><th>Name</th><th>URL</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody>
        <tr v-for="r in repos" :key="r.repo_key">
          <td>{{ r.repo_key }}</td>
          <td>{{ r.name }}</td>
          <td class="desc">{{ r.git_url }}</td>
          <td><span :class="['badge', r.status]">{{ r.status }}</span></td>
          <td><button @click="syncRepo(r.repo_key)">Sync</button></td>
        </tr>
      </tbody>
    </table>
    <p v-else>No repositories configured.</p>

    <h2 style="margin-top:2rem">Built-in Wiki KBs</h2>
    <table v-if="kbs.length">
      <thead><tr><th>Slug</th><th>Name</th><th>Docs</th><th>Members</th></tr></thead>
      <tbody>
        <tr v-for="k in kbs" :key="k.slug">
          <td>{{ k.slug }}</td>
          <td>{{ k.name }}</td>
          <td>{{ k.doc_count }}</td>
          <td>{{ k.member_count }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else>No Wiki KBs.</p>
  </div>
</template>
