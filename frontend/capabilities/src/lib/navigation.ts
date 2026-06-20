export function shouldShowPageHeader(activeNavKey: string, subRoute: string): boolean {
  return !(['scripts', 'services', 'workflow'].includes(activeNavKey) && Boolean(subRoute))
}
