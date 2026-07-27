import assert from 'node:assert/strict'
import test from 'node:test'

import { api } from '../src/api/client.ts'
import { useWorkflowArtifacts } from '../src/composables/useWorkflowArtifacts.ts'
import { artifactFormatLabel, artifactFormatOptions } from '../src/lib/workflowArtifactFormats.ts'

test('artifact format options use clear document labels', () => {
  assert.deepEqual(artifactFormatOptions, [
    { value: 'all', label: '全部' },
    { value: 'markdown', label: 'Markdown 文档' },
    { value: 'html', label: 'HTML 文档' },
  ])
  assert.equal(artifactFormatLabel('markdown'), 'Markdown 文档')
  assert.equal(artifactFormatLabel('html'), 'HTML 文档')
})

test('changing artifact format restarts from the first page and searches that format', async () => {
  const originalSearch = api.searchWorkflowArtifacts
  const params: Array<{ format?: string; offset?: number }> = []
  api.searchWorkflowArtifacts = async current => {
    params.push(current)
    return { items: [], total: 0 }
  }

  try {
    const artifacts = useWorkflowArtifacts(() => ({ profileKey: 'profile', workflowKey: 'workflow' }))
    artifacts.artifactPage.value = 3

    await artifacts.setArtifactFormat('html')

    assert.equal(artifacts.artifactPage.value, 1)
    assert.deepEqual(params.at(-1), { profile_key: 'profile', workflow_key: 'workflow', query: undefined, path: undefined, tags: [], format: 'html', limit: 50, offset: 0 })
  } finally {
    api.searchWorkflowArtifacts = originalSearch
  }
})
