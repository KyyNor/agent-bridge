import { toRaw } from 'vue'

export const SCHEMA_FIELD_TYPES = ['string', 'number', 'integer', 'boolean', 'array', 'object'] as const

export type SchemaFieldType = (typeof SCHEMA_FIELD_TYPES)[number]

export interface SchemaField {
  name: string
  type: SchemaFieldType
  required: boolean
  description: string
}

type JsonObject = Record<string, unknown>

export function cloneSchemaValue(value: Record<string, unknown>): Record<string, unknown> {
  return structuredClone(toRaw(value))
}

const COMPLEX_KEYWORDS = new Set([
  '$defs',
  '$ref',
  'allOf',
  'anyOf',
  'const',
  'contains',
  'contentEncoding',
  'contentMediaType',
  'contentSchema',
  'default',
  'dependentRequired',
  'dependentSchemas',
  'else',
  'enum',
  'examples',
  'format',
  'if',
  'items',
  'maxContains',
  'maxItems',
  'maxLength',
  'maxProperties',
  'maximum',
  'minContains',
  'minItems',
  'minLength',
  'minProperties',
  'minimum',
  'multipleOf',
  'not',
  'oneOf',
  'pattern',
  'patternProperties',
  'prefixItems',
  'propertyNames',
  'then',
  'unevaluatedItems',
  'unevaluatedProperties',
])

const SIMPLE_OBJECT_KEYS = [
  '$comment',
  '$id',
  '$schema',
  'additionalProperties',
  'deprecated',
  'description',
  'examples',
  'properties',
  'readOnly',
  'required',
  'title',
  'type',
  'writeOnly',
]

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

function hasOnlyKeys(value: JsonObject, allowed: string[]): boolean {
  return Object.keys(value).every(key => allowed.includes(key))
}

function isSimpleFieldSchema(value: unknown): value is JsonObject & { type: SchemaFieldType; description?: string } {
  if (!isObject(value)) return false
  if (!isSupportedFieldType(value.type)) return false
  if (!hasOnlyKeys(value, ['type', 'description'])) return false
  return value.description === undefined || typeof value.description === 'string'
}

export function isSimpleObjectSchema(schema: Record<string, unknown> | null | undefined): boolean {
  if (!isObject(schema)) return false
  if (schema.type !== 'object') return false
  if (schema.additionalProperties !== undefined && schema.additionalProperties !== false) return false
  if (!hasOnlyKeys(schema, SIMPLE_OBJECT_KEYS)) return false

  const properties = schema.properties
  if (properties === undefined || !isObject(properties)) return false
  if (!Object.values(properties).every(isSimpleFieldSchema)) return false

  const required = schema.required
  if (required !== undefined) {
    if (!Array.isArray(required)) return false
    if (!required.every(item => typeof item === 'string')) return false
    const propertyNames = new Set(Object.keys(properties))
    if (!required.every(item => propertyNames.has(item))) return false
  }

  for (const fieldSchema of Object.values(properties)) {
    if (!isObject(fieldSchema)) return false
    if (Object.keys(fieldSchema).some(key => COMPLEX_KEYWORDS.has(key))) return false
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
    type: value.type as SchemaFieldType,
    required: required.has(name),
    description: typeof value.description === 'string' ? value.description : '',
  }))
}

export function fieldsToSchema(
  fields: SchemaField[],
  source?: Record<string, unknown> | null,
): Record<string, unknown> {
  const properties = Object.fromEntries(
    fields.map(field => [
      field.name,
      {
        type: field.type,
        ...(field.description.trim() ? { description: field.description.trim() } : {}),
      },
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
    type: 'object',
    properties,
    ...(keepRequired ? { required } : {}),
  }
}
