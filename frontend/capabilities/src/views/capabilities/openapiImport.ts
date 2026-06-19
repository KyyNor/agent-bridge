import type { OpenApiTool } from '../../api/types'

export interface OpenApiImportState {
  operations: OpenApiTool[]
  selected: Set<string>
}

export function defaultOpenApiImportState(): OpenApiImportState {
  return { operations: [], selected: new Set<string>() }
}

export function toggleOperationSelection(state: OpenApiImportState, toolName: string): void {
  if (state.selected.has(toolName)) state.selected.delete(toolName)
  else state.selected.add(toolName)
}

export function selectedOperations(state: OpenApiImportState): OpenApiTool[] {
  return state.operations.filter(operation => state.selected.has(operation.tool_name))
}

export function editableOperation(operation: OpenApiTool): OpenApiTool {
  return {
    ...operation,
    input_schema: structuredClone(operation.input_schema || {}),
    request_mapping: structuredClone(operation.request_mapping || {}),
    response_schema: structuredClone(operation.response_schema || {}),
    tags: [...(operation.tags || [])],
    examples: structuredClone(operation.examples || []),
  }
}

export function buildOpenApiToolPayload(operation: OpenApiTool): OpenApiTool {
  return editableOperation(operation)
}
