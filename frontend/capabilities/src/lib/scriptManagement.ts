type Schema = Record<string, unknown> | null

export const DEFAULT_SCRIPT_CODE = 'def main(envelope):\n    return {}\n'

export interface ScriptEditableFields {
  script_key: string
  name: string
  description: string
  language: string
  code: string
  status: string
  owner_type: string
  owner_key: string
  input_schema: Record<string, unknown>
  output_schema: Schema
}

export interface ScriptSourceInfo {
  script_key: string
  source?: string
  is_builtin: boolean
}

export interface ScriptFormState {
  form: ScriptEditableFields
  outputSchemaEnabled: boolean
}

export function toScriptFormState(
  detail: Partial<ScriptEditableFields> & Pick<ScriptEditableFields, 'script_key'>,
  fallbackInputSchema: Record<string, unknown>,
): ScriptFormState {
  const outputSchema = detail.output_schema || null
  return {
    form: {
      script_key: detail.script_key,
      name: detail.name || '',
      description: detail.description || '',
      language: detail.language || 'python',
      code: detail.code || '',
      status: detail.status || 'active',
      owner_type: detail.owner_type || 'system',
      owner_key: detail.owner_key || '',
      input_schema: detail.input_schema || fallbackInputSchema,
      output_schema: outputSchema,
    },
    outputSchemaEnabled: !!outputSchema,
  }
}

export function toScriptUpsertPayload(
  form: ScriptEditableFields,
  outputSchemaEnabled: boolean,
): ScriptEditableFields {
  return {
    script_key: form.script_key,
    name: form.name,
    description: form.description,
    language: form.language,
    code: form.code,
    status: form.status,
    owner_type: form.owner_type,
    owner_key: form.owner_type === 'system' ? '' : form.owner_key,
    input_schema: form.input_schema,
    output_schema: outputSchemaEnabled ? form.output_schema : null,
  }
}

export function isBuiltInScriptFamily(item: ScriptSourceInfo): boolean {
  return item.is_builtin
}

export function isDefaultBuiltInScript(item: ScriptSourceInfo): boolean {
  return item.source === 'default'
}

export function canDeleteScript(item: ScriptSourceInfo): boolean {
  return !isBuiltInScriptFamily(item)
}

export function canDisableScript(item: ScriptSourceInfo): boolean {
  return !isBuiltInScriptFamily(item)
}

export function canEditScriptContract(item: ScriptSourceInfo): boolean {
  return !isBuiltInScriptFamily(item)
}

export function canResetScript(item: ScriptSourceInfo): boolean {
  return isBuiltInScriptFamily(item)
}

export function scriptResetPath(scriptKey: string): string {
  return `/scripts/${scriptKey}/reset`
}

export function mergeScriptDesignDraft(
  current: ScriptEditableFields,
  draft: Partial<ScriptEditableFields> & Pick<ScriptEditableFields, 'script_key'>,
): ScriptEditableFields {
  return {
    ...current,
    ...draft,
    output_schema: draft.output_schema == null ? current.output_schema : draft.output_schema,
  }
}
