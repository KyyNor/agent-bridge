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

test('buildServicePayload omits headers when editing and headers field is blank', () => {
  const payload = buildServicePayload(
    {
      ...defaultServiceForm(),
      service_key: 'mysql',
      name: 'MySQL MCP',
      endpoint_url: 'https://mysql.example.test/mcp',
      description: 'Reports',
      tags: 'database, reporting',
      headers: '',
    },
    'edit',
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
      headers: '{"Authorization":"Bearer token"}',
    },
    'create',
  )

  assert.deepEqual(payload, {
    service_key: 'mysql',
    name: 'MySQL MCP',
    endpoint_url: 'https://mysql.example.test/mcp',
    description: '',
    tags: [],
    visibility: 'group',
    headers: { Authorization: 'Bearer token' },
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

  assert.equal(form.headers, '')
  assert.equal(form.tags, 'database')
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
