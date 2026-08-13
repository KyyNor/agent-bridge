import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const router = readFileSync(resolve(root, 'src/router/index.ts'), 'utf8')
const app = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
const pageHeader = readFileSync(resolve(root, 'src/components/PageHeader.vue'), 'utf8')
const viteConfig = readFileSync(resolve(root, 'vite.config.ts'), 'utf8')

test('uses Vue Router History mode and has explicit deep-link routes', () => {
  assert.match(router, /createWebHistory\('\/agent-bridge\/'\)/)
  assert.match(router, /path: '\/services\/:routeKey\(\.\*\)\*'/)
  assert.match(router, /path: '\/workflow\/:routeKey\(\.\*\)\*'/)
  assert.match(router, /path: '\/agent-runs\/:routeKey\(\.\*\)\*'/)
  assert.match(router, /path: '\/:pathMatch\(\.\*\)\*'/)
  assert.match(viteConfig, /agent-bridge-dev-history-fallback/)
  assert.match(viteConfig, /startsWith\('\/agent-bridge\/'\)/)
})

test('App delegates view selection to RouterView and no longer owns fragment history', () => {
  assert.match(app, /<RouterView(?: v-else)? v-slot=/)
  assert.doesNotMatch(app, /hashchange|popstate|location\.hash|window\.history/)
  assert.doesNotMatch(app, /<DashboardView v-if/)
})

test('navigation configuration reflects the confirmed information architecture', () => {
  assert.match(app, /items: \[[\s\S]*key: 'dashboard', label: '平台概览'[\s\S]*key: 'profiles', label: '知识平面'[\s\S]*\],[\s\S]*label: '知识管理'/)
  assert.match(app, /label: '知识管理',[\s\S]*key: 'services'/)
  assert.doesNotMatch(app, /能力治理/)
  assert.doesNotMatch(app, /key: 'tools', label: '工具目录'/)
  assert.doesNotMatch(app, /key: 'tool-debug', label: '工具调试'/)
})

test('system management and access-control navigation are limited to maintenance administrators', () => {
  assert.match(app, /const adminOnlyNavigationKeys = new Set\(\['system-config', 'access-control'\]\)/)
  assert.match(app, /actorContext\.value\?\.is_maintenance_admin \|\| !adminOnlyNavigationKeys\.has\(item\.key\)/)
  assert.match(app, /!actor\.is_maintenance_admin && adminOnlyNavigationKeys\.has\(navKey\)/)
  assert.match(app, /router\.replace\(\{ name: 'dashboard' \}\)/)
})

test('secondary capability tool pages use the shared page-header return pattern', () => {
  assert.match(router, /path: '\/tools'.*backTo: '\/services'/)
  assert.match(router, /path: '\/tool-debug'.*backTo: '\/services'/)
  assert.match(app, /:show-back="Boolean\(navMeta\.backTo\)"/)
  assert.match(app, /@back="returnToParent"/)
  assert.match(pageHeader, /v-if="showBack"[^>]*@click="\$emit\('back'\)"/)
  assert.match(pageHeader, /<ArrowLeft/)
  assert.match(pageHeader, />\s*返回\s*</)
})
