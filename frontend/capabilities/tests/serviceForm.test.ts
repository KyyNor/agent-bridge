import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildOpenApiServicePayload,
  buildServicePayload,
  defaultServiceForm,
  defaultOpenApiServiceForm,
  parseHeadersJson,
  parseJsonObject,
  serviceToForm,
} from '../src/views/capabilities/serviceForm.ts'

test('parseHeadersJson returns undefined for blank input', () => {
  assert.equal(parseHeadersJson(''), undefined)
  assert.equal(parseHeadersJson('   '), undefined)
})

test('parseHeadersJson accepts a JSON object', () => {
  assert.deepEqual(parseHeadersJson('{"Authorization":"Bearer token","X-Tenant":"docs"}'), {
    Authorization: 'Bearer token',
    'X-Tenant': 'docs',
  })
})

test('parseHeadersJson rejects invalid JSON and non-object values', () => {
  assert.throws(() => parseHeadersJson('{bad'), /Header 必须是合法的 JSON 对象/)
  assert.throws(() => parseHeadersJson('[]'), /Header 必须是 JSON 对象/)
})

test('buildServicePayload omits updates when saved values remain blank', () => {
  const payload = buildServicePayload(
    {
      ...defaultServiceForm(),
      service_key: 'mysql',
      name: 'MySQL MCP',
      endpoint_url: 'https://mysql.example.test/mcp',
      description: 'Reports',
      tags: 'database, reporting',
      headers: [{ name: 'Authorization', value: '', saved: true }],
      savedHeaderNames: ['Authorization'],
    },
  )

  assert.deepEqual(payload, {
    service_key: 'mysql',
    name: 'MySQL MCP',
    endpoint_url: 'https://mysql.example.test/mcp',
    description: 'Reports',
    tags: ['database', 'reporting'],
    visibility: 'group',
  })
})

test('buildServicePayload includes headers when provided', () => {
  const payload = buildServicePayload(
    {
      ...defaultServiceForm(),
      service_key: 'mysql',
      name: 'MySQL MCP',
      endpoint_url: 'https://mysql.example.test/mcp',
      headers: [{ name: 'Authorization', value: 'Bearer token', saved: false }],
    },
  )

  assert.deepEqual(payload, {
    service_key: 'mysql',
    name: 'MySQL MCP',
    endpoint_url: 'https://mysql.example.test/mcp',
    description: '',
    tags: [],
    visibility: 'group',
    header_updates: { Authorization: 'Bearer token' },
  })
})

test('serviceToForm keeps redacted headers out of the editable secret field', () => {
  const form = serviceToForm({
    service_key: 'mysql',
    name: 'MySQL MCP',
    endpoint_url: 'https://mysql.example.test/mcp',
    description: 'Database tools',
    tags: ['database'],
    headers: { Authorization: '***' },
    status: 'enabled',
    created_by: 'root',
    created_at: '2026-06-18T00:00:00Z',
    updated_at: '2026-06-18T00:00:00Z',
    last_synced_at: null,
    last_error: null,
    owner_group_key: 'data-team',
    visibility: 'shared',
  })

  assert.deepEqual(form.headers, [{ name: 'Authorization', value: '', saved: true }])
  assert.equal(form.tags, 'database')
})

test('buildServicePayload updates, adds and deletes headers without resending kept values', () => {
  const form = defaultServiceForm()
  form.savedHeaderNames = ['Authorization', 'X-Tenant', 'X-Old']
  form.headers = [
    { name: 'Authorization', value: 'Bearer rotated', saved: true },
    { name: 'X-Tenant', value: '', saved: true },
    { name: 'X-New', value: 'new', saved: false },
  ]
  assert.deepEqual(buildServicePayload(form).header_updates, {
    Authorization: 'Bearer rotated',
    'X-New': 'new',
    'X-Old': null,
  })
  form.headers.push({ name: 'authorization', value: 'duplicate', saved: false })
  assert.throws(() => buildServicePayload(form), /Header 名称不能重复/)
})

test('parseJsonObject accepts blank and object JSON values with custom labels', () => {
  assert.equal(parseJsonObject('', '认证配置'), undefined)
  assert.deepEqual(parseJsonObject('{"type":"bearer","token":"t"}', '认证配置'), {
    type: 'bearer',
    token: 't',
  })
  assert.throws(() => parseJsonObject('[]', '认证配置'), /认证配置 必须是 JSON 对象/)
})

test('buildOpenApiServicePayload preserves blank secrets while editing', () => {
  const payload = buildOpenApiServicePayload(
    {
      ...defaultOpenApiServiceForm(),
      service_key: 'petstore',
      name: 'Petstore',
      base_url: 'https://api.example.test',
      spec_url: 'https://api.example.test/openapi.json',
      spec_content: '',
      auth_config: '',
      headers: '',
      description: 'Pet API',
      tags: 'pets, demo',
    },
    'edit',
  )

  assert.deepEqual(payload, {
    service_key: 'petstore',
    name: 'Petstore',
    base_url: 'https://api.example.test',
    spec_url: 'https://api.example.test/openapi.json',
    spec_content: '',
    description: 'Pet API',
    tags: ['pets', 'demo'],
    visibility: 'group',
  })
})

test('buildOpenApiServicePayload includes auth and headers when provided', () => {
  const payload = buildOpenApiServicePayload(
    {
      ...defaultOpenApiServiceForm(),
      service_key: 'crm',
      name: 'CRM',
      base_url: 'https://crm.example.test',
      auth_config: '{"type":"api_key","header":"X-API-Key","value":"secret"}',
      headers: '{"Accept":"application/json"}',
    },
    'create',
  )

  assert.deepEqual(payload, {
    service_key: 'crm',
    name: 'CRM',
    base_url: 'https://crm.example.test',
    spec_url: '',
    spec_content: '',
    description: '',
    tags: [],
    visibility: 'group',
    auth_config: { type: 'api_key', header: 'X-API-Key', value: 'secret' },
    headers: { Accept: 'application/json' },
  })
})
