/**
 * Tiny zero-dependency unified-diff parser.
 *
 * Parses the unified-diff text produced by the backend (`core/diff.py:text_diff`,
 * which wraps Python's `difflib.unified_diff`) into structured rows for rendering.
 * We avoid pulling in the `diff` npm package — the backend already computed the
 * patch, we only need to colourise it.
 */

export type DiffLineType = 'hunk' | 'add' | 'del' | 'ctx'

export interface DiffLine {
  type: DiffLineType
  /** Line text without the leading +/-/space marker. */
  text: string
  /** Original unified-diff header line for hunk rows (e.g. `@@ -1,3 +1,4 @@`). */
  header?: string
}

/**
 * Split unified-diff text into renderable rows. Lines that don't start with a
 * recognised marker (file headers `---`/`+++`, or stray content) are skipped,
 * matching how `git diff` output is normally displayed.
 */
export function parseUnifiedDiff(content: string): DiffLine[] {
  const lines = (content || '').split('\n')
  const rows: DiffLine[] = []
  let headerLines = 0
  for (const raw of lines) {
    if (raw === '') continue
    if (raw.startsWith('@@')) {
      rows.push({ type: 'hunk', text: raw, header: raw })
      continue
    }
    if (headerLines < 2 && (raw.startsWith('+++') || raw.startsWith('---'))) {
      // File header lines — skip, the caller passes explicit labels.
      headerLines++
      continue
    }
    const marker = raw[0]
    const text = raw.slice(1)
    if (marker === '+') rows.push({ type: 'add', text })
    else if (marker === '-') rows.push({ type: 'del', text })
    else if (marker === ' ') rows.push({ type: 'ctx', text })
    else if (raw.startsWith('\\') && raw.includes('No newline')) {
      // `\ No newline at end of file` marker — ignore.
      continue
    }
    // Anything else (leading whitespace, stray text) is dropped.
  }
  return rows
}

/** Count added/removed rows for a summary chip. */
export function diffStats(content: string): { added: number; removed: number } {
  const rows = parseUnifiedDiff(content)
  let added = 0
  let removed = 0
  for (const row of rows) {
    if (row.type === 'add') added++
    else if (row.type === 'del') removed++
  }
  return { added, removed }
}
