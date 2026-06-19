import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildOpenApiToolPayload,
  defaultOpenApiImportState,
  editableOperation,
  selectedOperations,
  toggleOperationSelection,
} from '../src/views/capabilities/openapiImport.ts'

const operation = {
  tool_name: 'list_pets',
  operation_id: 'listPets',
  method: 'GET',
  path: '/pets',
  display_name: 'List pets',
  description: 'List pets',
  input_schema: { type: 'object', properties: { limit: { type: 'integer' } } },
  request_mapping: { path: {}, query: { limit: 'limit' }, headers: {}, body: null },
  response_schema: {},
  tool_type: 'search',
  tags: ['pets'],
  examples: [],
}

test('toggleOperationSelection tracks candidate tools without mutating operations', () => {
  const state = defaultOpenApiImportState()
  state.operations = [operation]

  toggleOperationSelection(state, 'list_pets')

  assert.deepEqual([...state.selected], ['list_pets'])
  assert.equal(state.operations[0].description, 'List pets')
  assert.deepEqual(selectedOperations(state).map(item => item.tool_name), ['list_pets'])
})

test('editableOperation clones a preview candidate for admin edits', () => {
  const edit = editableOperation(operation)
  edit.description = 'Admin edited description'
  edit.tags.push('readonly')

  assert.equal(operation.description, 'List pets')
  assert.deepEqual(operation.tags, ['pets'])
  assert.equal(edit.description, 'Admin edited description')
})

test('buildOpenApiToolPayload keeps edited operation contract', () => {
  const payload = buildOpenApiToolPayload({
    ...editableOperation(operation),
    description: 'Admin edited description',
    tool_type: 'detail',
  })

  assert.equal(payload.description, 'Admin edited description')
  assert.equal(payload.tool_type, 'detail')
  assert.deepEqual(payload.request_mapping.query, { limit: 'limit' })
})
