import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import legacy from '@vitejs/plugin-legacy'

export default defineConfig({
  base: '/static/capabilities/',
  plugins: [
    vue(),
    tailwindcss(),
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
  },
  server: {
    proxy: {
      '/': 'http://127.0.0.1:8765',
    },
  },
})
