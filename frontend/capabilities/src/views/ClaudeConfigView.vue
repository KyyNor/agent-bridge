<script setup lang="ts">
import { ref } from 'vue'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Input } from '../components/ui/input'
import { Button } from '../components/ui/button'

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
  <div class="space-y-5">
    <Card class="border-border">
      <CardHeader>
        <CardTitle>Claude Code 接入</CardTitle>
      </CardHeader>
      <CardContent class="space-y-4">
        <p class="text-sm text-muted-foreground">为当前项目生成 MetaMCP 网关接入命令。</p>

        <div class="space-y-2">
          <label class="text-sm font-medium">作用范围</label>
          <select v-model="scope" class="w-full max-w-[300px] rounded-lg border border-input px-3 py-2 text-sm outline-none focus:border-primary">
            <option value="project">项目级 (.mcp.json)</option>
            <option value="user">用户级 (~/.mcp.json)</option>
          </select>
        </div>

        <div class="space-y-2">
          <label class="text-sm font-medium">Profile Key</label>
          <Input v-model="profileKey" placeholder="safe-readonly" class="max-w-[300px]" />
        </div>

        <div class="space-y-2">
          <label class="text-sm font-medium">Server URL</label>
          <Input v-model="serverUrl" placeholder="http://127.0.0.1:8765/mcp" class="max-w-[400px]" />
        </div>

        <Button @click="generateConfig">生成配置</Button>

        <pre v-if="result" class="overflow-x-auto rounded-lg bg-gray-900 p-4 font-mono text-sm text-gray-200">{{ result }}</pre>
        <div v-if="result" class="text-xs text-muted-foreground">
          在终端执行: <code class="rounded bg-secondary px-1 py-0.5">agent-bridge metamcp add --url {{ serverUrl }} --profile {{ profileKey || 'PROFILE' }} --scope {{ scope }}</code>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
