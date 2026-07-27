export type ArtifactFormat = 'all' | 'markdown' | 'html'

export const artifactFormatOptions: ReadonlyArray<{ value: ArtifactFormat; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'markdown', label: 'Markdown 文档' },
  { value: 'html', label: 'HTML 文档' },
]

export function artifactFormatLabel(format: Exclude<ArtifactFormat, 'all'>) {
  return format === 'html' ? 'HTML 文档' : 'Markdown 文档'
}

export function artifactFormatBadgeClass(format: Exclude<ArtifactFormat, 'all'>) {
  return format === 'html'
    ? 'border-warning/30 bg-warning-soft text-warning-soft-fg'
    : 'border-info/30 bg-info-soft text-info-soft-fg'
}
