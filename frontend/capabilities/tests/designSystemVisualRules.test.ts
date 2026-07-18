import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const read = (file: string) => readFileSync(resolve(root, file), 'utf8')
function sourceFiles(dir: string): string[] {
  return readdirSync(resolve(root, dir), { withFileTypes: true }).flatMap(entry => {
    const path = `${dir}/${entry.name}`
    return entry.isDirectory() ? sourceFiles(path) : /\.(vue|ts)$/.test(entry.name) ? [path] : []
  })
}

test('base.css exposes one four-level radius scale', () => {
  const css = read('src/styles/base.css')

  assert.match(css, /--radius-compact:\s*4px/)
  assert.match(css, /--radius-control:\s*6px/)
  assert.match(css, /--radius-card:\s*10px/)
  assert.match(css, /--radius-overlay:\s*14px/)
  assert.match(css, /--radius-sm:\s*var\(--radius-compact\)/)
  assert.match(css, /--radius-md:\s*var\(--radius-control\)/)
  assert.match(css, /--radius-lg:\s*var\(--radius-card\)/)
  assert.match(css, /--radius-xl:\s*var\(--radius-overlay\)/)
})

test('shared primitives use the radius level matching their role', () => {
  assert.match(read('src/components/ui/button/index.ts'), /rounded-md/)
  assert.match(read('src/components/ui/card/Card.vue'), /rounded-lg/)
  assert.match(read('src/components/ui/input/Input.vue'), /rounded-md/)
  assert.match(read('src/components/ui/select/SelectTrigger.vue'), /rounded-md/)
  assert.match(read('src/components/ui/dialog/DialogContent.vue'), /rounded-xl/)
  assert.match(read('src/components/StatusBadge.vue'), /rounded-sm/)
  assert.match(read('src/components/CategoryBadge.vue'), /rounded-sm/)
})

test('list rows share the muted hover and primary leading accent', () => {
  const css = read('src/styles/base.css')

  assert.match(css, /\.list-row-interactive,?[\s\S]*tbody\s*>\s*tr\s*\{[\s\S]*transition:[\s\S]*background-color/)
  assert.match(css, /\.list-row-interactive:hover,[\s\S]*tbody\s*>\s*tr:hover\s*\{[\s\S]*background-color:\s*color-mix\(in srgb, var\(--muted\) 70%/)
  assert.match(css, /box-shadow:\s*inset 2px 0 0 var\(--primary\)/)
  assert.match(css, /tbody\s*>\s*tr\s*\{[\s\S]*transition:/)
  assert.match(css, /#ph-actions\s*>\s*\[data-slot='button'\][\s\S]*height:\s*2\.25rem/)
  assert.match(read('src/components/ui/table/TableRow.vue'), /list-row-interactive/)
})

test('runtime components do not retain raw palette status colors', () => {
  const rawPalette = /(?:bg|text|border)-(?:white|black)(?:\/[\w.]+)?|(?:bg|text|border)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|gray|slate|zinc|neutral|stone)-\d+(?:\/[\w.]+)?/

  for (const file of sourceFiles('src')) {
    const source = read(file)
    assert.doesNotMatch(source, rawPalette, file)
    if (file !== 'src/styles/base.css') {
      assert.doesNotMatch(source, /#[0-9a-f]{3,8}/i, `${file}: direct hex`)
      assert.doesNotMatch(source, /\brgb\(/i, `${file}: direct rgb`)
    }
  }
})

test('runtime components use soft state tokens for destructive surfaces', () => {
  const legacyDestructiveSurface = /bg-destructive\/(?:5|10)/

  for (const file of sourceFiles('src')) {
    assert.doesNotMatch(read(file), legacyDestructiveSurface, file)
  }
})

test('page header owns a consistent control rhythm', () => {
  const pageHeader = read('src/components/PageHeader.vue')
  const segmentedTabs = read('src/components/SegmentedTabs.vue')

  assert.match(pageHeader, /id="ph-actions"[^>]*gap-2/)
  assert.match(pageHeader, /id="ph-filters"[^>]*gap-2/)
  assert.match(segmentedTabs, /inline-flex h-9/)
  assert.match(segmentedTabs, /'[^']*h-7[^']*rounded-sm/)
})
