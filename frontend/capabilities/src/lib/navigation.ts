export function shouldShowPageHeader(activeNavKey: string, subRoute: string): boolean {
  return !(['scripts', 'services', 'workflow', 'memory'].includes(activeNavKey) && Boolean(subRoute))
}
