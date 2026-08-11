import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 4320,
    proxy: {
      '/bridge/realtime': {
        target: 'http://127.0.0.1:8766',
        changeOrigin: false,
        rewrite: () => '/api/realtime',
      },
      '/bridge/cross-oracle': {
        target: 'http://127.0.0.1:8767',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/strategies': {
        target: 'http://127.0.0.1:8768',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/poly-gap': {
        target: 'http://127.0.0.1:8769',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/multi-market': {
        target: 'http://127.0.0.1:8770',
        changeOrigin: false,
        rewrite: () => '/state',
      },
    },
  },
})
