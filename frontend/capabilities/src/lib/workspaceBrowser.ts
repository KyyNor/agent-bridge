/** Web 工作台采用 Chrome 111 基线；未知内核保守检测必要特性。 */
export type WorkspaceMode = 'web' | 'terminal'
export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  return value === 'terminal' ? 'terminal' : 'web'
}
export function supportsWebWorkspace(ua: string, features: { hasOwn: boolean; clone: boolean; colorMix: boolean }): boolean {
  const chrome = /(?:Chrome|Chromium)\/(\d+)/.exec(ua)
  if (chrome && Number(chrome[1]) < 111) return false
  return features.hasOwn && features.clone && features.colorMix
}
export function currentWebWorkspaceSupport(): boolean {
  return supportsWebWorkspace(navigator.userAgent, {
    hasOwn: 'hasOwn' in Object,
    clone: typeof structuredClone === 'function',
    colorMix: typeof CSS !== 'undefined' && CSS.supports('color', 'color-mix' + '(in srgb, red, blue)'),
  })
}
