import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_SCRIPT_CODE,
  canDeleteScript,
  canDisableScript,
  canEditScriptContract,
  canResetScript,
  mergeScriptDesignDraft,
  scriptResetPath,
  toScriptFormState,
  toScriptUpsertPayload,
} from '../src/lib/scriptManagement.ts'
import type { ManagedScript } from '../src/api/types.ts'

const INPUT_SCHEMA = {
  type: 'object',
  properties: {
    repo: { type: 'string' },
  },
  required: ['repo'],
  additionalProperties: false,
} as const

const OUTPUT_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
  },
  required: ['summary'],
  additionalProperties: false,
} as const

test('new scripts start with the minimal executable main template', () => {
  assert.equal(DEFAULT_SCRIPT_CODE, 'def main(envelope):\n    return {}\n')
})

function script(overrides: Partial<ManagedScript> = {}): ManagedScript {
  return {
    script_key: 'custom.script',
    name: 'Custom Script',
    description: '',
    language: 'python',
    status: 'active',
    owner_type: 'system',
    owner_key: '',
    content_hash: 'hash',
    created_by: 'root',
    updated_by: 'root',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    input_schema: INPUT_SCHEMA,
    output_schema: null,
    is_builtin: false,
    source: 'database',
    code: 'def main(envelope):\n    return {}\n',
    code_preview: 'def main',
    ...overrides,
  }
}

test('script payload preserves output schema on save and detail rehydrate', () => {
  const detail = script({
    script_key: 'system.validate_workflow',
    output_schema: OUTPUT_SCHEMA,
    source: 'database',
  })

  const state = toScriptFormState(detail, INPUT_SCHEMA)
  assert.equal(state.outputSchemaEnabled, true)
  assert.deepEqual(state.form.output_schema, OUTPUT_SCHEMA)

  const payload = toScriptUpsertPayload(state.form, state.outputSchemaEnabled)
  assert.deepEqual(payload.output_schema, OUTPUT_SCHEMA)
  assert.deepEqual(payload.input_schema, INPUT_SCHEMA)
})

test('default built-ins are protected and database overrides remain resettable', () => {
  const defaultBuiltin = script({
    script_key: 'system.validate_workflow',
    source: 'default',
    is_builtin: true,
  })
  const overrideBuiltin = script({
    script_key: 'system.validate_workflow',
    source: 'database',
    is_builtin: true,
  })
  const prefixedUserScript = script({
    script_key: 'system.user_owned',
    source: 'database',
    is_builtin: false,
  })

  assert.equal(canDeleteScript(defaultBuiltin), false)
  assert.equal(canDisableScript(defaultBuiltin), false)
  assert.equal(canResetScript(defaultBuiltin), true)

  assert.equal(canResetScript(overrideBuiltin), true)
  assert.equal(canDeleteScript(prefixedUserScript), true)
  assert.equal(canDisableScript(prefixedUserScript), true)
  assert.equal(canResetScript(prefixedUserScript), false)
})

test('built-in scripts only allow code edits while user scripts keep contract fields editable', () => {
  assert.equal(canEditScriptContract(script({ is_builtin: true })), false)
  assert.equal(canEditScriptContract(script({ is_builtin: false })), true)
})

test('design adoption preserves existing output schema when the agent omits or nulls it', () => {
  const current = toScriptFormState(script({ output_schema: OUTPUT_SCHEMA }), INPUT_SCHEMA).form
  const draft = {
    ...current,
    name: 'Updated by agent',
    output_schema: undefined,
  }

  const merged = mergeScriptDesignDraft(current, draft)

  assert.equal(merged.name, 'Updated by agent')
  assert.deepEqual(merged.output_schema, OUTPUT_SCHEMA)
  assert.deepEqual(mergeScriptDesignDraft(current, { ...draft, output_schema: null }).output_schema, OUTPUT_SCHEMA)
  assert.deepEqual(toScriptUpsertPayload(merged, true).output_schema, OUTPUT_SCHEMA)
})

test('reset path uses scripts reset endpoint', () => {
  assert.equal(scriptResetPath('system.validate_workflow'), '/scripts/system.validate_workflow/reset')
})
