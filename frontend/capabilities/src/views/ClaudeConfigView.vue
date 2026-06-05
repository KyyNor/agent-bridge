<script setup lang="ts">
import { ref } from 'vue'

const scope = ref('project')
const profileKey = ref('')
const serverUrl = ref('http://127.0.0.1:8765/mcp')
const result = ref('')

function generateConfig() {
  const config = {
    mcpServers: {
      'agent-capability-hub': {
        type: 'http',
        url: serverUrl.value,
        headers: { 'X-Agent-Bridge-MetaMCP-Profile': profileKey.value },
      },
    },
  }
  result.value = JSON.stringify(config, null, 2)
}
</script>

<template>
  <div class="view">
    <h2>Claude Code MetaMCP Configuration</h2>
    <div class="form-group">
      <label>Scope:</label>
      <select v-model="scope">
        <option value="project">Project (.mcp.json)</option>
        <option value="user">User (~/.mcp.json)</option>
      </select>
    </div>
    <div class="form-group">
      <label>Profile Key:</label>
      <input v-model="profileKey" placeholder="e.g. safe-readonly" />
    </div>
    <div class="form-group">
      <label>Server URL:</label>
      <input v-model="serverUrl" placeholder="http://127.0.0.1:8765/mcp" />
    </div>
    <button @click="generateConfig">Generate Config</button>
    <pre v-if="result" class="config-output">{{ result }}</pre>
    <p class="hint">Run <code>agent-bridge metamcp add --url {{ serverUrl }} --profile {{ profileKey || 'PROFILE' }} --scope {{ scope }}</code> to apply.</p>
  </div>
</template>
