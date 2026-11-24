import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8010',
      '/health': 'http://127.0.0.1:8010',
    },
  },
  build: {
    outDir: '../后端/web/dist',
    emptyOutDir: true,
    rolldownOptions: { input: ['index.html', 'testbench.html'] },
  },
})
