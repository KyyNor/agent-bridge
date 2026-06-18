import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildServicePayload,
  defaultServiceForm,
  parseHeadersJson,
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
  })

  assert.equal(form.headers, '')
  assert.equal(form.tags, 'database')
})
