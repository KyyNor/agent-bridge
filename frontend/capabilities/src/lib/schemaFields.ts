import { toRaw } from 'vue'

export const SCHEMA_FIELD_TYPES = ['string', 'number', 'integer', 'boolean', 'array', 'object', 'null'] as const

export type SchemaFieldType = (typeof SCHEMA_FIELD_TYPES)[number]
export type SchemaFieldDisplayType = SchemaFieldType | 'any' | 'union' | 'reference'

export interface SchemaField {
  name: string
  type: SchemaFieldDisplayType
  required: boolean
  description: string
  /**
   * The complete property schema when it contains keywords that the compact
   * row editor cannot represent directly. Keeping this alongside the display
   * fields lets the editor rename a property without flattening its schema.
   */
  schema?: Record<string, unknown>
}

type JsonObject = Record<string, unknown>

export function cloneSchemaValue(value: Record<string, unknown>): Record<string, unknown> {
  return structuredClone(toRaw(value))
}

// JSON Schema 2020-12 keywords, plus the widely used draft-07 aliases. The
// compact editor is intentionally conservative about unknown/vendor keys, but
// it should not classify a valid standard schema as "advanced" just because it
// uses a constraint that is not represented by the first row of controls.
const JSON_SCHEMA_KEYS = [
  '$anchor',
  '$comment',
  '$defs',
  '$dynamicAnchor',
  '$dynamicRef',
  '$id',
  '$ref',
  '$schema',
  '$vocabulary',
  'additionalItems',
  'additionalProperties',
  'allOf',
  'anyOf',
  'const',
  'contains',
  'contentEncoding',
  'contentMediaType',
  'contentSchema',
  'default',
  'definitions',
  'dependentRequired',
  'dependentSchemas',
  'dependencies',
  'deprecated',
  'description',
  'else',
  'enum',
  'examples',
  'exclusiveMaximum',
  'exclusiveMinimum',
  'format',
  'id',
  'if',
  'items',
  'maxContains',
  'maxItems',
  'maxLength',
  'maxProperties',
  'maximum',
  'maxUnevaluatedItems',
  'maxUnevaluatedProperties',
  'minContains',
  'minItems',
  'minLength',
  'minProperties',
  'minimum',
  'minUnevaluatedItems',
  'minUnevaluatedProperties',
  'multipleOf',
  'not',
  'oneOf',
  'pattern',
  'patternProperties',
  'prefixItems',
  'properties',
  'propertyNames',
  'readOnly',
  'required',
  'then',
  'title',
  'type',
  'unevaluatedItems',
  'unevaluatedProperties',
  'uniqueItems',
  'writeOnly',
] as const

const JSON_SCHEMA_KEY_SET = new Set<string>(JSON_SCHEMA_KEYS)

const SIMPLE_OBJECT_KEYS = JSON_SCHEMA_KEYS

const FIELD_DISPLAY_TYPE_LABELS: Record<string, string> = {
  any: 'any',
  union: 'union',
  reference: '$ref',
}

function hasOnlySchemaKeywords(value: JsonObject): boolean {
  return Object.keys(value).every(key => JSON_SCHEMA_KEY_SET.has(key))
}

export function isEditableFieldSchema(value: unknown): value is JsonObject {
  return isObject(value) && hasOnlySchemaKeywords(value)
}

export function schemaFieldDisplayType(schema: Record<string, unknown>): SchemaFieldDisplayType {
  if (isSupportedFieldType(schema.type)) return schema.type
  if (Array.isArray(schema.type)) return 'union'
  if ('$ref' in schema || '$dynamicRef' in schema) return 'reference'
  if ('anyOf' in schema || 'oneOf' in schema || 'allOf' in schema) return 'union'
  return 'any'
}

function hasRichFieldSchema(schema: JsonObject): boolean {
  return Object.keys(schema).some(key => key !== 'type' && key !== 'description') || !isSupportedFieldType(schema.type)
}

const FIELD_DISPLAY_TYPE_TEXT: Record<string, string> = {
  ...FIELD_DISPLAY_TYPE_LABELS,
}

export function schemaFieldTypeLabel(type: SchemaFieldDisplayType): string {
  return FIELD_DISPLAY_TYPE_TEXT[type] || type
}

function isObject(value: unknown): value is JsonObject {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

export function parseSchemaObjectText(value: string):
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; message: string } {
  const text = value.trim()
  if (!text) return { ok: true, value: fieldsToSchema([]) }
  try {
    const parsed = JSON.parse(text)
    if (!isObject(parsed)) return { ok: false, message: 'Schema 必须是 JSON 对象' }
    return { ok: true, value: parsed }
  } catch {
    return { ok: false, message: '高级 JSON 不是合法对象' }
  }
}

export function validateSchemaFieldNames(fields: SchemaField[], label: string): string {
  const names = fields.map(field => field.name.trim())
  if (names.some(name => !name)) return `${label}字段名不能为空`
  if (new Set(names).size !== names.length) return `${label}字段名不能重复`
  return ''
}

function isSupportedFieldType(value: unknown): value is SchemaFieldType {
  return typeof value === 'string' && (SCHEMA_FIELD_TYPES as readonly string[]).includes(value)
}

export function isSchemaFieldType(value: unknown): value is SchemaFieldType {
  return isSupportedFieldType(value)
}

function hasOnlyKeys(value: JsonObject, allowed: readonly string[]): boolean {
  return Object.keys(value).every(key => allowed.includes(key))
}

export function isSimpleObjectSchema(schema: Record<string, unknown> | null | undefined): boolean {
  if (!isObject(schema)) return false
  const rootType = schema.type
  if (
    rootType !== undefined
    && rootType !== 'object'
    && !(
      Array.isArray(rootType)
      && rootType.length > 0
      && rootType.includes('object')
      && rootType.every(isSupportedFieldType)
    )
  ) return false
  const additionalProperties = schema.additionalProperties
  if (
    additionalProperties !== undefined
    && typeof additionalProperties !== 'boolean'
    && !isEditableFieldSchema(additionalProperties)
  ) return false
  if (!hasOnlyKeys(schema, SIMPLE_OBJECT_KEYS)) return false

  const properties = schema.properties
  if (properties === undefined || !isObject(properties)) return false
  if (!Object.values(properties).every(isEditableFieldSchema)) return false

  const required = schema.required
  if (required !== undefined) {
    if (!Array.isArray(required)) return false
    if (!required.every(item => typeof item === 'string')) return false
    const propertyNames = new Set(Object.keys(properties))
    if (!required.every(item => propertyNames.has(item))) return false
  }

  return true
}

export function schemaToFields(schema: Record<string, unknown> | null | undefined): SchemaField[] {
  if (!isSimpleObjectSchema(schema)) return []

  const simpleSchema = schema as JsonObject
  const properties = simpleSchema.properties as Record<string, JsonObject>
  const required = new Set(
    Array.isArray(simpleSchema.required)
      ? simpleSchema.required.filter((item): item is string => typeof item === 'string')
      : [],
  )

  return Object.entries(properties).map(([name, value]) => ({
    name,
    type: schemaFieldDisplayType(value),
    required: required.has(name),
    description: typeof value.description === 'string' ? value.description : '',
    ...(hasRichFieldSchema(value) ? { schema: cloneSchemaValue(value) } : {}),
  }))
}

function schemaForField(field: SchemaField): JsonObject {
  const source = field.schema && isObject(field.schema) ? cloneSchemaValue(field.schema) : {}
  const hasExplicitType = 'type' in source

  if (isSupportedFieldType(field.type) && (!field.schema || hasExplicitType)) {
    source.type = field.type
  }
  if (field.description.trim()) source.description = field.description.trim()
  else delete source.description
  return source
}

export function fieldsToSchema(
  fields: SchemaField[],
  source?: Record<string, unknown> | null,
): Record<string, unknown> {
  const properties = Object.fromEntries(
    fields.map(field => [
      field.name,
      schemaForField(field),
    ]),
  )

  const base = source
    ? Object.fromEntries(
        Object.entries(source).filter(([key]) => !['type', 'properties', 'required'].includes(key)),
      )
    : { additionalProperties: false }
  const required = fields.filter(field => field.required).map(field => field.name)
  const keepRequired = source === undefined || source === null || 'required' in source || required.length > 0

  return {
    ...base,
    ...(source
      ? (Object.prototype.hasOwnProperty.call(source, 'type') ? { type: source.type } : {})
      : { type: 'object' }),
    properties,
    ...(keepRequired ? { required } : {}),
  }
}
