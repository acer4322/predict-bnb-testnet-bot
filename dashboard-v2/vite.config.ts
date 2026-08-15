import { randomBytes } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { serviceManagerPlugin } from './service-manager'

const repoRoot = fileURLToPath(new URL('..', import.meta.url))

function isLoopback(address: string | undefined) {
  const value = String(address || '').toLowerCase()
  return value === '127.0.0.1' || value === '::1' || value === '::ffff:127.0.0.1'
}

function localhostControlGuard(): Plugin {
  const token = randomBytes(32).toString('hex')
  return {
    name: 'btc5m-localhost-control-guard',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = String(req.url || '')
        if (url === '/control/session' || url.startsWith('/control/session?')) {
          if (!isLoopback(req.socket.remoteAddress)) {
            res.statusCode = 403
            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            res.end(JSON.stringify({ ok: false, error: 'Dashboard write controls are localhost-only' }))
            return
          }
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json; charset=utf-8')
          res.setHeader('Cache-Control', 'no-store')
          res.end(JSON.stringify({ ok: true, token }))
          return
        }
        if (url.startsWith('/control/')) {
          const supplied = String(req.headers['x-btc-lab-control'] || '')
          if (!isLoopback(req.socket.remoteAddress) || supplied !== token) {
            res.statusCode = 403
            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            res.end(JSON.stringify({ ok: false, error: 'Dashboard write rejected: localhost session token required' }))
            return
          }
        }
        next()
      })
    },
  }
}

export default defineConfig({
  plugins: [localhostControlGuard(), serviceManagerPlugin(repoRoot), react()],
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
      '/bridge/predict-fun': {
        target: 'http://127.0.0.1:8771',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/eth-live': {
        target: 'http://127.0.0.1:8772',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/bnb-live': {
        target: 'http://127.0.0.1:8773',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/eth-clone': {
        target: 'http://127.0.0.1:8774',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/bnb-clone': {
        target: 'http://127.0.0.1:8775',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/wallet-shadow-health': {
        target: 'http://127.0.0.1:8776',
        changeOrigin: false,
        rewrite: () => '/health',
      },
      '/bridge/wallet-taker-signals': {
        target: 'http://127.0.0.1:8777',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/wallet-maker-book-inference-eth5m': {
        target: 'http://127.0.0.1:8779',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/wallet-maker-book-inference': {
        target: 'http://127.0.0.1:8778',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/wallet-shadow': {
        target: 'http://127.0.0.1:8776',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/bridge/echtgeld-health': {
        target: 'http://127.0.0.1:8781',
        changeOrigin: false,
        rewrite: () => '/health',
      },
      '/bridge/echtgeld': {
        target: 'http://127.0.0.1:8781',
        changeOrigin: false,
        rewrite: () => '/state',
      },
      '/control/echtgeld/pause': {
        target: 'http://127.0.0.1:8781',
        changeOrigin: false,
        rewrite: () => '/control/pause',
      },
      '/control/echtgeld/resume': {
        target: 'http://127.0.0.1:8781',
        changeOrigin: false,
        rewrite: () => '/control/resume',
      },
      '/control/echtgeld/settings': {
        target: 'http://127.0.0.1:8781',
        changeOrigin: false,
        rewrite: () => '/control/settings',
      },
      '/control/target-taker-v1': {
        target: 'http://127.0.0.1:8776',
        changeOrigin: false,
        rewrite: () => '/settings',
      },
      '/control/btc-live': {
        target: 'http://127.0.0.1:8769',
        changeOrigin: false,
        rewrite: () => '/settings',
      },
      '/control/eth-live': {
        target: 'http://127.0.0.1:8772',
        changeOrigin: false,
        rewrite: () => '/settings',
      },
      '/control/bnb-live': {
        target: 'http://127.0.0.1:8773',
        changeOrigin: false,
        rewrite: () => '/settings',
      },
      '/control/eth-clone': {
        target: 'http://127.0.0.1:8774',
        changeOrigin: false,
        rewrite: () => '/settings',
      },
      '/control/bnb-clone': {
        target: 'http://127.0.0.1:8775',
        changeOrigin: false,
        rewrite: () => '/settings',
      },
    },
  },
})
