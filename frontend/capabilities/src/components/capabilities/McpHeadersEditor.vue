<script setup lang="ts">
import { Plus, Trash2 } from '@lucide/vue'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import type { McpHeaderRow } from '../../views/capabilities/serviceForm'

const rows = defineModel<McpHeaderRow[]>({ required: true })

function addHeader() {
  rows.value = [...rows.value, { name: '', value: '', saved: false }]
}

function removeHeader(index: number) {
  rows.value = rows.value.filter((_, rowIndex) => index !== rowIndex)
}
</script>

<template>
  <fieldset class="space-y-2">
    <legend class="text-sm font-medium">请求 Header</legend>
    <p v-if="rows.some(row => row.saved)" class="text-xs text-muted-foreground">已保存的值保持隐藏。留空保留原值，填写新值即可替换。</p>
    <div v-for="(row, index) in rows" :key="row.saved ? row.name : index" class="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2">
      <Input
        v-model="row.name"
        :aria-label="`第 ${index + 1} 个 Header 名称`"
        :readonly="row.saved"
        placeholder="Header 名称"
        autocomplete="off"
        required
      />
      <Input
        v-model="row.value"
        :aria-label="`第 ${index + 1} 个 Header 值`"
        type="password"
        :placeholder="row.saved ? '已保存' : 'Header 值'"
        autocomplete="new-password"
        spellcheck="false"
      />
      <Button type="button" variant="ghost" size="icon" :aria-label="`删除第 ${index + 1} 个 Header`" @click="removeHeader(index)">
        <Trash2 :size="16" />
      </Button>
    </div>
    <Button type="button" variant="outline" size="sm" @click="addHeader">
      <Plus :size="14" />
      添加 Header
    </Button>
  </fieldset>
</template>
