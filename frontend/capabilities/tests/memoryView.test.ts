import assert from 'node:assert/strict'
import test from 'node:test'

type FetchCall = {
  url: string
  init?: RequestInit
}

async function loadApiWithFetchMock(responseBody: unknown = {}) {
  const calls: FetchCall[] = []
  ;(globalThis as unknown as { window: Record<string, string> }).window = {}
  ;(globalThis as unknown as { fetch: typeof fetch }).fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init })
    return {
      ok: true,
      json: async () => responseBody,
      text: async () => JSON.stringify(responseBody),
    } as Response
  }) as typeof fetch

  const moduleUrl = `../src/api/client.ts?memory-test=${Date.now()}-${Math.random()}`
  const { api } = await import(moduleUrl)
  return { api, calls }
}

test('memory api methods use the expected endpoints and payloads', async () => {
  const { api, calls } = await loadApiWithFetchMock({
    profile_key: 'dev',
    block_key: 'dev-memory',
    enabled: true,
  })

  await api.listMemoryBlocks()
  await api.createMemoryBlock({ block_key: 'dev-memory', name: 'Dev Memory', description: 'Project memory' })
  await api.getMemoryBlockHealth('dev-memory')
  await api.getMemoryDashboardStatus('dev-memory')
  await api.startMemoryDashboard('dev-memory')
  await api.stopMemoryDashboard('dev-memory')
  await api.touchMemoryDashboard('dev-memory')
  await api.searchMemoryBlock('dev-memory', 'tool recall', 5)
  await api.getMemoryTimeline('dev-memory', 7, 'cursor-1')
  await api.getProfileMemory('dev')
  await api.setProfileMemory('dev', null, true)

  assert.equal(calls[0].url, '/memory/blocks')
  assert.equal(calls[1].url, '/memory/blocks')
  assert.equal(calls[1].init?.method, 'POST')
  assert.equal(calls[1].init?.body, JSON.stringify({
    block_key: 'dev-memory',
    name: 'Dev Memory',
    description: 'Project memory',
  }))
  assert.equal(calls[2].url, '/memory/blocks/dev-memory/health')
  assert.equal(calls[3].url, '/memory/blocks/dev-memory/dashboard')
  assert.equal(calls[4].url, '/memory/blocks/dev-memory/dashboard/start')
  assert.equal(calls[4].init?.method, 'POST')
  assert.equal(calls[5].url, '/memory/blocks/dev-memory/dashboard/stop')
  assert.equal(calls[5].init?.method, 'POST')
  assert.equal(calls[6].url, '/memory/blocks/dev-memory/dashboard/touch')
  assert.equal(calls[6].init?.method, 'POST')
  assert.equal(calls[7].url, '/memory/blocks/dev-memory/search?q=tool+recall&limit=5')
  assert.equal(calls[8].url, '/memory/blocks/dev-memory/timeline?limit=7&cursor=cursor-1')
  assert.equal(calls[9].url, '/capability-profiles/dev/memory')
  assert.equal(calls[10].url, '/capability-profiles/dev/memory')
  assert.equal(calls[10].init?.method, 'PUT')
  assert.equal(calls[10].init?.body, JSON.stringify({ block_key: null, enabled: true }))
})
