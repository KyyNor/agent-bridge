import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return sourceFiles(path)
    return /\.(css|ts|vue)$/.test(entry.name) ? [path] : []
  })
}

test('uses the Tailwind v3 build chain with explicit Chrome 90 targets', () => {
  const packageJson = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'))
  const viteConfig = readFileSync(resolve(root, 'vite.config.ts'), 'utf8')
  const tailwindConfig = readFileSync(resolve(root, 'tailwind.config.cjs'), 'utf8')

  assert.match(packageJson.devDependencies.tailwindcss, /^\^3\.4\./)
  assert.match(packageJson.dependencies['tailwind-merge'], /^\^2\.6\./)
  assert.equal(packageJson.devDependencies['@tailwindcss/vite'], undefined)
  assert.equal(packageJson.dependencies['tw-animate-css'], undefined)
  assert.match(viteConfig, /targets: \['chrome >= 90'\]/)
  assert.match(viteConfig, /cssTarget: 'chrome90'/)
  assert.match(tailwindConfig, /tailwindcss-animate/)
  assert.match(tailwindConfig, /--\$\{name\}-opacity-/)
})

test('source styles avoid CSS features that are unavailable in Chrome 90', () => {
  const sources = sourceFiles(resolve(root, 'src'))
    .map(path => readFileSync(path, 'utf8'))
    .join('\n')

  for (const unsupported of [
    /color-mix\(/,
    /oklch\(/,
    /:has\(/,
    /@container\b/,
    /field-sizing\s*:/,
    /\bfield-sizing-content\b/,
    /scrollbar-gutter\s*:/,
  ]) {
    assert.doesNotMatch(sources, unsupported)
  }
})
