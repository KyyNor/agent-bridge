import type { ArtifactTreeNode, WorkflowArtifact } from '../api/types'

export interface ArtifactTreeRow {
  type: 'folder' | 'artifact'
  depth: number
  path: string
  segment?: string
  count?: number
  artifact?: WorkflowArtifact
}

function countArtifacts(node: ArtifactTreeNode): number {
  return node.artifacts.length + node.children.reduce((sum, child) => sum + countArtifacts(child), 0)
}

export function buildArtifactTree(artifacts: WorkflowArtifact[]): ArtifactTreeNode[] {
  const root: ArtifactTreeNode[] = []
  for (const item of artifacts) {
    const segments = item.path.split('/').filter(Boolean)
    const folderSegments = segments.slice(0, -1)
    let nodes = root
    let acc = ''
    let parentNode: ArtifactTreeNode | undefined
    folderSegments.forEach((segment) => {
      acc = acc ? `${acc}/${segment}` : segment
      let node = nodes.find(child => child.segment === segment)
      if (!node) {
        node = { segment, path: acc, children: [], artifacts: [] }
        nodes.push(node)
      }
      parentNode = node
      nodes = node.children
    })
    if (folderSegments.length) {
      if (parentNode) parentNode.artifacts.push(item)
    } else {
      // Root-level artifacts have no folder row. Keep a stable synthetic group
      // so they remain visible without treating the filename as a directory.
      let rootGroup = root.find(node => node.path === '')
      if (!rootGroup) {
        rootGroup = { segment: '根目录', path: '', children: [], artifacts: [] }
        root.unshift(rootGroup)
      }
      rootGroup.artifacts.push(item)
    }
  }
  const sortNodes = (nodes: ArtifactTreeNode[]) => {
    nodes.sort((a, b) => a.segment.localeCompare(b.segment))
    nodes.forEach(child => sortNodes(child.children))
  }
  sortNodes(root)
  return root
}

export function flattenArtifactTree(
  nodes: ArtifactTreeNode[],
  collapsedPaths: ReadonlySet<string>,
): ArtifactTreeRow[] {
  const rows: ArtifactTreeRow[] = []
  const walk = (current: ArtifactTreeNode[], depth: number) => {
    for (const node of current) {
      rows.push({ type: 'folder', depth, path: node.path, segment: node.segment, count: countArtifacts(node) })
      if (collapsedPaths.has(node.path)) continue
      for (const item of node.artifacts) {
        rows.push({ type: 'artifact', depth: depth + 1, path: item.path, artifact: item })
      }
      walk(node.children, depth + 1)
    }
  }
  walk(nodes, 0)
  return rows
}
