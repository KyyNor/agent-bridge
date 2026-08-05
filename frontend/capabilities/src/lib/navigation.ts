export function shouldShowPageHeader(activeNavKey: string, subRoute: string): boolean {
  return !(['scripts', 'services', 'workflow', 'memory', 'knowledge', 'profiles', 'code-repos', 'business-ledgers', 'agent-runs'].includes(activeNavKey) && Boolean(subRoute))
}

export type NavigationGuard = (to: string, from: string) => boolean | Promise<boolean>
export interface NavigationOptions {
  replace?: boolean
}
export type NavigationController = (target: string, options?: NavigationOptions) => Promise<boolean>

let navigationController: NavigationController = async (target, options = {}) => {
  if (options.replace) window.history.replaceState(window.history.state, '', `#${target}`)
  else window.location.hash = target
  return true
}
const navigationGuards = new Set<NavigationGuard>()

export function normalizeHash(value: string): string {
  return value.replace(/^#/, '').replace(/^\/+|\/+$/g, '') || 'dashboard'
}

export function currentHash(): string {
  return normalizeHash(window.location.hash)
}

export function installNavigationController(controller: NavigationController): () => void {
  const previous = navigationController
  navigationController = controller
  return () => {
    if (navigationController === controller) navigationController = previous
  }
}

export function registerNavigationGuard(guard: NavigationGuard): () => void {
  navigationGuards.add(guard)
  return () => navigationGuards.delete(guard)
}

export async function canNavigate(to: string, from = currentHash()): Promise<boolean> {
  const target = normalizeHash(to)
  const source = normalizeHash(from)
  if (target === source) return true
  for (const guard of [...navigationGuards]) {
    if (!await guard(target, source)) return false
  }
  return true
}

export function navigateTo(target: string, options?: NavigationOptions): Promise<boolean> {
  return navigationController(normalizeHash(target), options)
}

export interface ParsedSubRoute {
  segments: string[]
  query: URLSearchParams
}

export function parseSubRoute(routeKey: string): ParsedSubRoute {
  const [path, query = ''] = routeKey.split('?', 2)
  return {
    segments: path.split('/').filter(Boolean),
    query: new URLSearchParams(query),
  }
}

export function returnToHash(targetHash: string, returnTo?: string | null): string {
  const target = normalizeHash(targetHash)
  if (!returnTo) return target
  const [path, query = ''] = target.split('?', 2)
  const params = new URLSearchParams(query)
  params.set('returnTo', normalizeHash(returnTo))
  return `${path}?${params.toString()}`
}

export function routeReturnTo(routeKey: string): string {
  return parseSubRoute(routeKey).query.get('returnTo') || ''
}

export function buildAgentRunHash(runKey: string, returnTo?: string | null): string {
  return returnToHash(`agent-runs/${runKey}`, returnTo)
}

export function buildScriptRunHash(scriptKey: string, runId: string, returnTo?: string | null): string {
  return returnToHash(`scripts/${scriptKey}/run/${runId}`, returnTo)
}

export function buildWorkflowTaskProgressHash(workflowKey: string, runId: string): string {
  return `workflow/${workflowKey}/progress/${runId}`
}
