/** 文件侧栏与目录选择器共用的路径与大小规则。 */
export const MAX_TERMINAL_FILE_BYTES = 32 * 1024 * 1024
export function childPath(parent: string, name: string): string {
  return `${parent.replace(/\/$/, '')}/${name}`.replace(/^\//, parent.startsWith('/') ? '/' : '')
}
export function parentPath(path: string): string {
  const parts = path.replace(/\/$/, '').split('/')
  parts.pop()
  return parts.join('/') || (path.startsWith('/') ? '/' : '')
}
export function fileSizeLabel(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`
}
