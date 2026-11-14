import type { BackendCatalogEntry } from '../api/types'

export function isWeknoraBackend(slug: string | null | undefined, backends: BackendCatalogEntry[]): boolean {
  return !!slug && backends.some(backend => backend.slug === slug && backend.backend_type === 'weknora')
}

/** 仅已知非 Weknora 后端或用户显式清空才置 null；后端类型无法判定时保留已保存的 Agent，避免误清空。 */
export function resolveDefaultAgentId(options: {
  backendSlug: string
  agentId: string
  backends: BackendCatalogEntry[]
  savedAgentId: string | null | undefined
}): string | null {
  const { backendSlug, agentId, backends, savedAgentId } = options
  if (isWeknoraBackend(backendSlug, backends)) return agentId || null
  const known = backends.some(backend => backend.slug === backendSlug)
  if (known) return null
  return savedAgentId ?? null
}