/** 网页终端配色与浏览器偏好；Pi / DSH 共用，不上传服务端。 */
import type { ITheme } from '@xterm/xterm'
import themes from '../styles/terminalThemes.json'

const STORAGE_KEY = 'agent-bridge:workspace-terminal:theme'
export const TERMINAL_THEMES: { id: string; label: string; colors: ITheme }[] = themes

export function resolveTerminalTheme(id: unknown) {
  return TERMINAL_THEMES.find(theme => theme.id === id) || TERMINAL_THEMES[0]!
}

export function readTerminalTheme(): string {
  try {
    return resolveTerminalTheme(window.localStorage.getItem(STORAGE_KEY)).id
  } catch {
    return 'midnight'
  }
}

export function saveTerminalTheme(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, resolveTerminalTheme(id).id)
  } catch {
    // 隐私模式或配额限制只影响记忆，仍允许本次切换配色。
  }
}
