import type { McpService } from '../../api/types'

export type ServiceFormMode = 'create' | 'edit'

export interface ServiceForm {
  service_key: string
  name: string
  endpoint_url: string
  description: string
  tags: string
  headers: string
}

export interface ServicePayload {
  service_key: string
  name: string
  endpoint_url: string
  description: string
  tags: string[]
  headers?: Record<string, unknown>
}

export function defaultServiceForm(): ServiceForm {
  return {
    service_key: '',
    name: '',
    endpoint_url: '',
    description: '',
    tags: '',
    headers: '',
  }
}

export function serviceToForm(service: McpService): ServiceForm {
  return {
    service_key: service.service_key,
    name: service.name,
    endpoint_url: service.endpoint_url,
    description: service.description || '',
    tags: service.tags.join(', '),
    headers: '',
  }
}

export function parseHeadersJson(value: string): Record<string, unknown> | undefined {
  const trimmed = value.trim()
  if (!trimmed) return undefined

  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch (error) {
    throw new Error('Header 必须是合法的 JSON 对象')
  }

  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Header 必须是 JSON 对象')
  }

  return parsed as Record<string, unknown>
}

export function buildServicePayload(form: ServiceForm, mode: ServiceFormMode): ServicePayload {
  const payload: ServicePayload = {
    service_key: form.service_key.trim(),
    name: form.name.trim(),
    endpoint_url: form.endpoint_url.trim(),
    description: form.description.trim(),
    tags: form.tags.split(',').map(tag => tag.trim()).filter(Boolean),
  }
  const headers = parseHeadersJson(form.headers)
  if (headers !== undefined || mode === 'create') {
    if (headers !== undefined) payload.headers = headers
  }
  return payload
}
