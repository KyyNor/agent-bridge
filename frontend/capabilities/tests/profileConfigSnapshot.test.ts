import assert from 'node:assert/strict'
import test from 'node:test'
import { profileConfigDraftKey } from '../src/views/capabilities/profileConfigSnapshot.ts'

const base = {
  sourceRules: [
    { source_type: 'mcp_service', source_key: 'docs', effect: 'allow' as const },
    { source_type: 'mcp_service', source_key: 'search', effect: 'allow' as const },
  ],
  resourceRules: [
    { resource_type: 'wiki_kb', resource_key: 'handbook' },
    { resource_type: 'code_repo', resource_key: 'bridge' },
  ],
  memoryBlockKey: 'team-memory',
  pins: [{ service_key: 'docs', tool_type: 'overview' }],
  pinMode: 'ratio' as const,
  pinRatio: 10,
  pinCount: 3,
  manualNotes: '',
}

test('normalizes rule order', () => {
  const reordered = {
    ...base,
    sourceRules: [...base.sourceRules].reverse(),
    resourceRules: [...base.resourceRules].reverse(),
  }
  assert.equal(profileConfigDraftKey(base), profileConfigDraftKey(reordered))
})

test('changes when a draft field or manual notes changes', () => {
  assert.notEqual(profileConfigDraftKey(base), profileConfigDraftKey({ ...base, memoryBlockKey: '' }))
  assert.notEqual(profileConfigDraftKey(base), profileConfigDraftKey({ ...base, manualNotes: '只读范围' }))
})
