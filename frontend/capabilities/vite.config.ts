import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: '/static/capabilities/',
  plugins: [vue(), tailwindcss()],
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
