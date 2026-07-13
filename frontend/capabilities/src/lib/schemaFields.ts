export const SCHEMA_FIELD_TYPES = ['string', 'number', 'integer', 'boolean', 'array', 'object'] as const

export type SchemaFieldType = (typeof SCHEMA_FIELD_TYPES)[number]

export interface SchemaField {
  name: string
  type: SchemaFieldType
  required: boolean
  description: string
}

type JsonObject = Record<string, unknown>

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

function isObject(value: unknown): value is JsonObject {
  return !!value && typeof value === 'object' && !Array.isArray(value)
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
  if (!hasOnlyKeys(schema, ['type', 'properties', 'required', 'additionalProperties', 'description'])) return false

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

export function fieldsToSchema(fields: SchemaField[]): Record<string, unknown> {
  const properties = Object.fromEntries(
    fields.map(field => [
      field.name,
      {
        type: field.type,
        ...(field.description.trim() ? { description: field.description.trim() } : {}),
      },
    ]),
  )

  return {
    type: 'object',
    properties,
    required: fields.filter(field => field.required).map(field => field.name),
    additionalProperties: false,
  }
}
