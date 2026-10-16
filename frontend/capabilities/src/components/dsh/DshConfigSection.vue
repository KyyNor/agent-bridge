<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Plus, Trash2 } from '@lucide/vue'
import { api } from '../../api/client'
import type { AccessGroup, DshGroupConfig, DshRuntimeConfig, PiVersions } from '../../api/types'
import { formatLocalDatetime } from '../../lib/time'
import { Card, CardContent } from '../ui/card'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from '../ui/dialog'
import StatusBadge from '../StatusBadge.vue'
import PiVersionsList from './PiVersionsList.vue'

const runtimeConfig = ref<DshRuntimeConfig | null>(null)

// 上限 10M 只为挡住误输入（如把字节数当 token 数填），与服务端校验保持一致。
function isTokenLimit(value: number) {
  return Number.isInteger(value) && value >= 0 && value <= 10_000_000
}
const runtimeForm = ref({ web_command: '', pi_web_command: '', idle_timeout_minutes: 120, base_url: '', models: [''], context_window: 0, max_tokens: 0 })
const runtimeSaving = ref(false)
const runtimeError = ref('')
const runtimeMessage = ref('')
const piVersions = ref<PiVersions | null>(null)

const groupConfigs = ref<DshGroupConfig[]>([])
const groups = ref<AccessGroup[]>([])
const groupError = ref('')
const showGroupDialog = ref(false)
const groupSaving = ref(false)
const groupDialogError = ref('')
const editingGroupKey = ref<string | null>(null)
const groupExpectedEditToken = ref<string | null>('')
const groupForm = ref({
  group_key: '',
  linux_user: '',
  default_model: '',
  api_key: '',
  clear_api_key: false,
})

const configuredGroupKeys = computed(() => new Set(groupConfigs.value.map(item => item.group_key)))
const availableGroupOptions = computed(() =>
  groups.value.filter(group => group.status === 'active' && !configuredGroupKeys.value.has(group.group_key)),
)
const modelOptions = computed(() =>
  runtimeConfig.value?.available_models?.length ? runtimeConfig.value.available_models : [],
)
const idleTimeoutValid = computed(
  () => Number.isInteger(runtimeForm.value.idle_timeout_minutes) && runtimeForm.value.idle_timeout_minutes >= 1,
)
const modelsValid = computed(() => runtimeForm.value.models.every(item => item.trim().length > 0))
// 0 表示不声明，由 DSH / Pi 沿用各自默认（DSH 262144、Pi 128000）。
const contextWindowValid = computed(() => isTokenLimit(runtimeForm.value.context_window))
// 单次输出上限，同样 0 = 不声明（DSH 32768、Pi 16384）。
const maxTokensValid = computed(() => isTokenLimit(runtimeForm.value.max_tokens))
const defaultModelValid = computed(
  () => !groupForm.value.default_model || modelOptions.value.includes(groupForm.value.default_model),
)

onMounted(async () => {
  await Promise.all([loadRuntimeConfig(), loadGroupConfigs(), loadGroups(), loadPiVersions()])
})

async function loadRuntimeConfig() {
  try {
    const config = await api.getDshRuntimeConfig()
    runtimeConfig.value = config
    runtimeForm.value = {
      web_command: config.web_command,
      pi_web_command: config.pi_web_command,
      idle_timeout_minutes: config.idle_timeout_minutes,
      base_url: config.base_url,
      models: config.available_models.length ? [...config.available_models] : [''],
      context_window: config.context_window ?? 0,
      max_tokens: config.max_tokens ?? 0,
    }
    runtimeError.value = ''
  } catch (e: any) {
    runtimeConfig.value = null
    runtimeError.value = e.message || '无法加载工作台运行配置'
  }
}

async function loadPiVersions() {
  try {
    piVersions.value = await api.getPiVersions()
  } catch {
    piVersions.value = null
  }
}

async function loadGroupConfigs() {
  try {
    groupConfigs.value = await api.listDshGroupConfigs()
    groupError.value = ''
  } catch (e: any) {
    groupConfigs.value = []
    groupError.value = e.message || '无法加载工作台组配置'
  }
}

async function loadGroups() {
  try {
    groups.value = await api.listAccessGroups()
  } catch {
    groups.value = []
  }
}

function addModelRow() {
  runtimeForm.value.models = [...runtimeForm.value.models, '']
}

function removeModelRow(index: number) {
  const remaining = runtimeForm.value.models.filter((_, itemIndex) => itemIndex !== index)
  runtimeForm.value.models = remaining.length ? remaining : ['']
}

async function saveRuntimeConfig() {
  if (!runtimeConfig.value || !idleTimeoutValid.value || !modelsValid.value || !contextWindowValid.value || !maxTokensValid.value) return
  runtimeSaving.value = true
  runtimeError.value = ''
  runtimeMessage.value = ''
  try {
    const saved = await api.saveDshRuntimeConfig({
      web_command: runtimeForm.value.web_command,
      pi_web_command: runtimeForm.value.pi_web_command,
      idle_timeout_minutes: runtimeForm.value.idle_timeout_minutes,
      base_url: runtimeForm.value.base_url.trim(),
      available_models: runtimeForm.value.models.map(item => item.trim()).filter(Boolean),
      context_window: runtimeForm.value.context_window,
      max_tokens: runtimeForm.value.max_tokens,
      expected_edit_token: runtimeConfig.value.edit_token,
    })
    runtimeConfig.value = saved
    runtimeForm.value = {
      web_command: saved.web_command,
      pi_web_command: saved.pi_web_command,
      idle_timeout_minutes: saved.idle_timeout_minutes,
      base_url: saved.base_url,
      models: saved.available_models.length ? [...saved.available_models] : [''],
      context_window: saved.context_window ?? 0,
      max_tokens: saved.max_tokens ?? 0,
    }
    runtimeMessage.value = '运行配置已保存，下次启动工作台时生效'
  } catch (e: any) {
    runtimeError.value = e.message || '保存失败'
  } finally {
    runtimeSaving.value = false
  }
}

function openCreateDialog() {
  editingGroupKey.value = null
  groupExpectedEditToken.value = ''
  groupDialogError.value = ''
  groupForm.value = {
    group_key: availableGroupOptions.value[0]?.group_key || '',
    linux_user: '',
    default_model: '',
    api_key: '',
    clear_api_key: false,
  }
  showGroupDialog.value = true
}

function openEditDialog(config: DshGroupConfig) {
  editingGroupKey.value = config.group_key
  groupExpectedEditToken.value = config.edit_token
  groupDialogError.value = ''
  groupForm.value = {
    group_key: config.group_key,
    linux_user: config.linux_user,
    default_model: config.default_model,
    api_key: '',
    clear_api_key: false,
  }
  showGroupDialog.value = true
}

async function saveGroupConfig() {
  if (!defaultModelValid.value) {
    groupDialogError.value = '默认模型必须在全局可用模型列表中'
    return
  }
  groupSaving.value = true
  groupDialogError.value = ''
  try {
    const groupKey = editingGroupKey.value || groupForm.value.group_key
    const saved = await api.saveDshGroupConfig(groupKey, {
      linux_user: groupForm.value.linux_user.trim() || groupKey,
      default_model: groupForm.value.default_model.trim(),
      api_key: groupForm.value.api_key || null,
      clear_api_key: groupForm.value.clear_api_key,
      expected_edit_token: groupExpectedEditToken.value,
    })
    const index = groupConfigs.value.findIndex(item => item.group_key === saved.group_key)
    if (index >= 0) groupConfigs.value[index] = saved
    else groupConfigs.value.push(saved)
    showGroupDialog.value = false
  } catch (e: any) {
    groupDialogError.value = e.message || '保存失败'
  } finally {
    groupSaving.value = false
  }
}
</script>

<template>
  <!-- 工作台 Runtime 运行配置：DSH 与 Pi Agent 共用全局与组级配置 -->
  <Card>
    <CardContent class="space-y-4 p-5">
      <div class="flex items-center justify-between gap-4">
        <div>
          <div class="text-sm font-medium">Agent 工作台 Runtime</div>
          <div class="mt-1 text-xs text-muted-foreground">
            用户级 Coding Agent 工作台的进程托管：动态端口、空闲回收与按组注入模型配置；组级配置由 DeepSeek Harness 与 Pi Agent 共享
          </div>
        </div>
        <Button variant="outline" size="sm" @click="loadRuntimeConfig(); loadGroupConfigs(); loadPiVersions()">刷新</Button>
      </div>

      <div v-if="runtimeConfig" class="space-y-4">
        <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
          <div class="text-sm">DSH 启动命令模板</div>
          <div class="space-y-1">
            <Input v-model="runtimeForm.web_command" placeholder="dsh web {patch} --host 127.0.0.1 --port {port} --no-open" class="font-mono text-xs" />
            <div class="text-xs text-muted-foreground">
              占位符 <code class="font-mono">{port}</code>（动态端口）与 <code class="font-mono">{patch}</code>（进入工作台时注入能力平面配置）；命令含 <code class="font-mono">--no-open</code> 可避免 DSH 自启浏览器
            </div>
          </div>
        </div>
        <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
          <div class="text-sm">Pi 启动命令模板</div>
          <div class="space-y-1">
            <Input
              v-model="runtimeForm.pi_web_command"
              :placeholder="piVersions?.default_web_command || 'pi-web --no-open --port {port} --hostname 127.0.0.1'"
              class="font-mono text-xs"
            />
            <div class="text-xs text-muted-foreground">
              Pi Agent 工作台的启动命令，只支持 <code class="font-mono">{port}</code> 占位符（动态端口）；默认
              <code class="font-mono">{{ piVersions?.default_web_command || 'pi-web --no-open --port {port} --hostname 127.0.0.1' }}</code>
            </div>
          </div>
        </div>
        <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
          <div class="text-sm">Base URL <span class="text-xs text-muted-foreground">(公共)</span></div>
          <div class="space-y-1">
            <Input v-model="runtimeForm.base_url" placeholder="留空继承「公共模型配置」的 Base URL" class="font-mono text-xs" />
            <div v-if="runtimeConfig.resolved_base_url" class="text-xs text-muted-foreground">
              当前生效：<span class="font-mono">{{ runtimeConfig.resolved_base_url }}</span>
              <span v-if="runtimeConfig.base_url_source === 'public_model_config'">（继承自公共模型配置）</span>
            </div>
            <div v-else class="text-xs text-destructive">尚未配置 Base URL，DSH 将使用自身默认供应商</div>
          </div>
        </div>
        <div class="grid grid-cols-[12rem_1fr] items-start gap-4">
          <div class="pt-1 text-sm">可用模型 <span class="text-xs text-muted-foreground">(公共)</span></div>
          <div class="space-y-2">
            <div v-for="(_, index) in runtimeForm.models" :key="index" class="flex items-center gap-2">
              <Input
                :model-value="runtimeForm.models[index]"
                placeholder="模型 ID，如 deepseek-flash"
                class="font-mono text-xs"
                @update:model-value="runtimeForm.models[index] = String($event || '')"
              />
              <Button variant="outline" size="sm" :disabled="runtimeForm.models.length === 1 && !runtimeForm.models[0]" @click="removeModelRow(index)">
                <Trash2 :size="14" />
              </Button>
            </div>
            <div class="flex items-center gap-3">
              <Button variant="outline" size="sm" @click="addModelRow()">
                <Plus :size="14" class="mr-1" />添加模型
              </Button>
              <span v-if="!modelsValid" class="text-xs text-destructive">模型 ID 不能为空</span>
            </div>
          </div>
        </div>
        <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
          <div class="text-sm">上下文长度 <span class="text-xs text-muted-foreground">(公共)</span></div>
          <div class="space-y-1">
            <Input
              v-model.number="runtimeForm.context_window"
              type="number"
              min="0"
              max="10000000"
              placeholder="留空或填 0 表示不声明"
              class="w-56 font-mono text-sm"
            />
            <div v-if="contextWindowValid" class="text-xs text-muted-foreground">
              以 <code class="font-mono">contextWindow</code> 逐模型注入到上面列出的模型，DSH 与 Pi 同时生效；0 表示不声明，沿用各自默认（DSH 262144、Pi 128000）
            </div>
            <div v-else class="text-xs text-destructive">请输入 0-10000000 的整数</div>
          </div>
        </div>
        <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
          <div class="text-sm">单次输出上限 <span class="text-xs text-muted-foreground">(公共)</span></div>
          <div class="space-y-1">
            <Input
              v-model.number="runtimeForm.max_tokens"
              type="number"
              min="0"
              max="10000000"
              placeholder="留空或填 0 表示不声明"
              class="w-56 font-mono text-sm"
            />
            <div v-if="maxTokensValid" class="text-xs text-muted-foreground">
              以 <code class="font-mono">maxTokens</code> 逐模型注入，DSH 与 Pi 同时生效；0 表示不声明，沿用各自默认（DSH 32768、Pi 16384）
            </div>
            <div v-else class="text-xs text-destructive">请输入 0-10000000 的整数</div>
          </div>
        </div>
        <div class="grid grid-cols-[12rem_1fr] items-center gap-4">
          <div class="text-sm">空闲回收阈值 <span class="text-xs text-muted-foreground">(分钟)</span></div>
          <div class="flex items-center gap-3">
            <Input v-model.number="runtimeForm.idle_timeout_minutes" type="number" min="1" class="w-32 font-mono text-sm" />
            <span v-if="idleTimeoutValid" class="text-xs text-muted-foreground">长期无访问的实例会被自动停止，用户 DSH 配置与 session 数据保留</span>
            <span v-else class="text-xs text-destructive">请输入 ≥1 的整数</span>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <Button size="sm" :disabled="runtimeSaving || !idleTimeoutValid || !modelsValid || !contextWindowValid || !maxTokensValid" @click="saveRuntimeConfig()">
            {{ runtimeSaving ? '保存中...' : '保存运行配置' }}
          </Button>
          <span v-if="runtimeError" class="text-xs text-destructive">{{ runtimeError }}</span>
          <span v-else-if="runtimeMessage" class="text-xs text-success">{{ runtimeMessage }}</span>
          <span v-else-if="runtimeConfig.updated_at" class="text-xs text-muted-foreground">更新于 {{ formatLocalDatetime(runtimeConfig.updated_at) }}</span>
        </div>
        <div class="text-xs text-muted-foreground">
          公共模型接入与 API Key 对两种工作台同时生效；API Key 只经进程环境变量传递，不落盘。
        </div>

        <div class="border-t border-border pt-4">
          <div class="mb-2 text-xs font-medium text-muted-foreground">Pi 依赖版本（托管）</div>
          <PiVersionsList :versions="piVersions" />
        </div>
      </div>
      <div v-else class="py-4 text-center text-sm text-muted-foreground">{{ runtimeError || '无法获取工作台运行配置' }}</div>

      <!-- 组级配置 -->
      <div class="border-t border-border pt-4">
        <div class="mb-3 flex items-center justify-between">
          <div class="text-xs font-medium text-muted-foreground">组级配置（Linux 用户 / 默认模型 / API Key，DeepSeek Harness 与 Pi Agent 共用）</div>
          <Button size="sm" variant="outline" :disabled="availableGroupOptions.length === 0" @click="openCreateDialog()">新增组配置</Button>
        </div>
        <div v-if="groupError" class="py-2 text-xs text-destructive">{{ groupError }}</div>
        <div v-if="groupConfigs.length === 0" class="py-4 text-center text-xs text-muted-foreground">
          尚无组级工作台配置；配置后该组成员即可启动自己的工作台
        </div>
        <div v-else class="rounded-md border border-border">
          <table class="w-full">
            <thead>
              <tr class="border-b border-border bg-muted/30">
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">小组</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Linux 用户</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">默认模型</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">API Key</th>
                <th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">更新时间</th>
                <th class="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="config in groupConfigs" :key="config.group_key" class="border-b border-border/60">
                <td class="px-3 py-2 font-mono text-sm">{{ config.group_key }}</td>
                <td class="px-3 py-2 font-mono text-xs">{{ config.linux_user }}</td>
                <td class="px-3 py-2 font-mono text-xs">{{ config.default_model || '—' }}</td>
                <td class="px-3 py-2">
                  <StatusBadge :status="config.api_key_set ? 'enabled' : 'disabled'" :label="config.api_key_set ? '已配置' : '未配置'" />
                </td>
                <td class="px-3 py-2 text-xs text-muted-foreground">{{ formatLocalDatetime(config.updated_at) }}</td>
                <td class="px-3 py-2 text-right">
                  <Button size="sm" variant="outline" @click="openEditDialog(config)">编辑</Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 组配置编辑弹窗 -->
      <Dialog v-model:open="showGroupDialog">
        <DialogContent class="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{{ editingGroupKey ? '编辑组级工作台配置' : '新增组级工作台配置' }}</DialogTitle>
          </DialogHeader>
          <div class="space-y-4">
            <div v-if="!editingGroupKey" class="grid grid-cols-[8rem_1fr] items-center gap-3">
              <div class="text-sm">小组</div>
              <select v-model="groupForm.group_key" class="h-9 rounded-md border border-input bg-background px-3 text-sm">
                <option v-for="group in availableGroupOptions" :key="group.group_key" :value="group.group_key">
                  {{ group.group_key }}（{{ group.name }}）
                </option>
              </select>
            </div>
            <div class="grid grid-cols-[8rem_1fr] items-center gap-3">
              <div class="text-sm">Linux 用户</div>
              <div class="flex items-center gap-2">
                <Input v-model="groupForm.linux_user" :placeholder="groupForm.group_key" class="font-mono text-xs" />
                <span class="text-xs text-muted-foreground">留空使用小组标识；工作台进程以该用户身份运行</span>
              </div>
            </div>
            <div class="grid grid-cols-[8rem_1fr] items-center gap-3">
              <div class="text-sm">默认模型</div>
              <div class="flex items-center gap-2">
                <select
                  v-if="modelOptions.length"
                  v-model="groupForm.default_model"
                  class="h-9 rounded-md border border-input bg-background px-3 font-mono text-xs"
                >
                  <option value="">不指定</option>
                  <option v-for="model in modelOptions" :key="model" :value="model">{{ model }}</option>
                </select>
                <Input v-else v-model="groupForm.default_model" placeholder="请先在运行配置中添加可用模型" class="font-mono text-xs" />
                <span v-if="!defaultModelValid" class="text-xs text-destructive">不在全局可用模型列表中</span>
              </div>
            </div>
            <div class="grid grid-cols-[8rem_1fr] items-center gap-3">
              <div class="text-sm">API Key</div>
              <div class="flex items-center gap-3">
                <Input v-model="groupForm.api_key" type="password" placeholder="留空保持不变" class="font-mono text-xs" :disabled="groupForm.clear_api_key" />
                <label class="flex items-center gap-2 text-xs text-muted-foreground"><input v-model="groupForm.clear_api_key" type="checkbox" class="h-4 w-4" />清除</label>
              </div>
            </div>
            <div v-if="groupDialogError" class="text-xs text-destructive">{{ groupDialogError }}</div>
          </div>
          <DialogFooter>
            <DialogClose as-child>
              <Button variant="outline" size="sm">取消</Button>
            </DialogClose>
            <Button size="sm" :disabled="groupSaving || !defaultModelValid" @click="saveGroupConfig()">
              {{ groupSaving ? '保存中...' : '保存' }}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </CardContent>
  </Card>
</template>
