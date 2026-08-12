import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import legacy from '@vitejs/plugin-legacy'

export default defineConfig({
  base: '/static/capabilities/',
  plugins: [
    vue(),
    {
      name: 'agent-bridge-dev-history-fallback',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          if (req.method === 'GET' && req.url?.split('?', 1)[0].startsWith('/agent-bridge/')) {
            req.url = '/static/capabilities/index.html'
          }
          next()
        })
      },
    },
    // 内网定制浏览器内核较低（Chrome 90），需要 polyfill ES2021+ API
    // 如 Object.hasOwn (Chrome 93+)、Array.prototype.at (Chrome 92+) 等
    legacy({
      targets: ['chrome >= 90'],
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: '../../src/agent_bridge/static/capabilities',
    emptyOutDir: true,
    cssTarget: 'chrome90',
  },
  server: {
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:8765',
      },
    },
  },
})
