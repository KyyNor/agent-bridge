/** Web 工作台采用 Chrome 111 基线；未知内核保守检测必要特性。 */
const MIN_WEB_CHROMIUM_VERSION = 111

export type WorkspaceMode = 'web' | 'terminal'
export function normalizeWorkspaceMode(value: unknown): WorkspaceMode {
  return value === 'terminal' ? 'terminal' : 'web'
}
export function webWorkspaceUnavailableReason(ua: string, features: { hasOwn: boolean; clone: boolean; colorMix: boolean }): string | null {
  const chrome = /(?:Chrome|Chromium)\/(\d+)/.exec(ua)
  if (chrome && Number(chrome[1]) < MIN_WEB_CHROMIUM_VERSION) {
    return `浏览器内核版本低于 Chrome ${MIN_WEB_CHROMIUM_VERSION}，无法使用 Web 模式。`
  }
  if (!features.hasOwn || !features.clone || !features.colorMix) {
    return '浏览器缺少所需功能，无法使用 Web 模式。'
  }
  return null
}
export function currentWebWorkspaceUnavailableReason(): string | null {
  return webWorkspaceUnavailableReason(navigator.userAgent, {
    hasOwn: 'hasOwn' in Object,
    clone: typeof structuredClone === 'function',
    colorMix: typeof CSS !== 'undefined' && CSS.supports('color', 'color-mix' + '(in srgb, red, blue)'),
  })
}
