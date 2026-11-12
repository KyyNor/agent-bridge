import type { McpService, OpenApiService, ResourceVisibility } from '../../api/types'

export type ServiceFormMode = 'create' | 'edit'
export type ServiceSourceType = 'mcp_service' | 'openapi_service'

export interface McpHeaderRow {
  name: string
  value: string
  saved: boolean
}

export interface ServiceForm {
  service_key: string
  name: string
  endpoint_url: string
  description: string
  tags: string
  headers: McpHeaderRow[]
  savedHeaderNames: string[]
  visibility: ResourceVisibility
}

export interface ServicePayload {
  service_key: string
  name: string
  endpoint_url: string
  description: string
  tags: string[]
  header_updates?: Record<string, string | null>
  visibility: ResourceVisibility
}

export interface OpenApiServiceForm {
  service_key: string
  name: string
  base_url: string
  spec_url: string
  spec_content: string
  auth_config: string
  headers: string
  description: string
  tags: string
  visibility: ResourceVisibility
}

export interface OpenApiServicePayload {
  service_key: string
  name: string
  base_url: string
  spec_url: string
  spec_content: string
  description: string
  tags: string[]
  auth_config?: Record<string, unknown>
  headers?: Record<string, unknown>
  visibility: ResourceVisibility
}

export function defaultServiceForm(): ServiceForm {
  return {
    service_key: '',
    name: '',
    endpoint_url: '',
    description: '',
    tags: '',
    headers: [],
    savedHeaderNames: [],
    visibility: 'group',
  }
}

export function defaultOpenApiServiceForm(): OpenApiServiceForm {
  return {
    service_key: '',
    name: '',
    base_url: '',
    spec_url: '',
    spec_content: '',
    auth_config: '',
    headers: '',
    description: '',
    tags: '',
    visibility: 'group',
  }
}

export function serviceToForm(service: McpService): ServiceForm {
  const savedHeaderNames = Object.keys(service.headers ?? {})
  return {
    service_key: service.service_key,
    name: service.name,
    endpoint_url: service.endpoint_url,
    description: service.description || '',
    tags: service.tags.join(', '),
    headers: savedHeaderNames.map(name => ({ name, value: '', saved: true })),
    savedHeaderNames,
    visibility: service.visibility,
  }
}

export function openApiServiceToForm(service: OpenApiService): OpenApiServiceForm {
  return {
    service_key: service.service_key,
    name: service.name,
    base_url: service.base_url,
    spec_url: service.spec_url || '',
    spec_content: service.spec_content || '',
    auth_config: '',
    headers: '',
    description: service.description || '',
    tags: service.tags.join(', '),
    visibility: service.visibility,
  }
}

export function parseHeadersJson(value: string): Record<string, unknown> | undefined {
  return parseJsonObject(value, 'Header')
}

export function parseJsonObject(value: string, label: string): Record<string, unknown> | undefined {
  const trimmed = value.trim()
  if (!trimmed) return undefined

  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch (error) {
    throw new Error(`${label} 必须是合法的 JSON 对象`)
  }

  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${label} 必须是 JSON 对象`)
  }

  return parsed as Record<string, unknown>
}

export function buildServicePayload(form: ServiceForm): ServicePayload {
  const payload: ServicePayload = {
    service_key: form.service_key.trim(),
    name: form.name.trim(),
    endpoint_url: form.endpoint_url.trim(),
    description: form.description.trim(),
    tags: form.tags.split(',').map(tag => tag.trim()).filter(Boolean),
    visibility: form.visibility,
  }
  const names = new Set<string>()
  const updates = new Map<string, string | null>()
  for (const header of form.headers) {
    const name = header.name.trim()
    const key = name.toLowerCase()
    if (!name) throw new Error('请输入 Header 名称')
    if (names.has(key)) throw new Error('Header 名称不能重复（不区分大小写）')
    names.add(key)
    if (!header.saved || header.value !== '') updates.set(name, header.value)
  }
  for (const name of form.savedHeaderNames) {
    if (!names.has(name.toLowerCase())) updates.set(name, null)
  }
  if (updates.size) payload.header_updates = Object.fromEntries(updates)
  return payload
}

export function buildOpenApiServicePayload(form: OpenApiServiceForm, mode: ServiceFormMode): OpenApiServicePayload {
  const payload: OpenApiServicePayload = {
    service_key: form.service_key.trim(),
    name: form.name.trim(),
    base_url: form.base_url.trim(),
    spec_url: form.spec_url.trim(),
    spec_content: form.spec_content.trim(),
    description: form.description.trim(),
    tags: form.tags.split(',').map(tag => tag.trim()).filter(Boolean),
    visibility: form.visibility,
  }
  const authConfig = parseJsonObject(form.auth_config, '认证配置')
  const headers = parseHeadersJson(form.headers)
  if (authConfig !== undefined || mode === 'create') {
    if (authConfig !== undefined) payload.auth_config = authConfig
  }
  if (headers !== undefined || mode === 'create') {
    if (headers !== undefined) payload.headers = headers
  }
  return payload
}
