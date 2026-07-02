export function shouldShowPageHeader(activeNavKey: string, subRoute: string): boolean {
  return !(['scripts', 'services', 'workflow', 'memory'].includes(activeNavKey) && Boolean(subRoute))
}

export function buildWorkflowTaskProgressHash(workflowKey: string, runId: string): string {
  return `workflow/${workflowKey}/progress/${runId}`
}
