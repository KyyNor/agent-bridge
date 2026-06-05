import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/static/capabilities/',
  plugins: [vue()],
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
