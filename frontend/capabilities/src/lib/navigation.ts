export function shouldShowPageHeader(activeNavKey: string, subRoute: string): boolean {
  return !(activeNavKey === 'scripts' && Boolean(subRoute))
}
