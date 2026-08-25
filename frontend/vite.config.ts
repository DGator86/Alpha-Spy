import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

/*
 * Three build targets, one application.
 *
 *   vps      (default)  Served by FastAPI from the dashboard's static directory
 *                       under /static/. Same-origin API. This is the bundle the
 *                       wheel ships, and the one CI checks for staleness.
 *   vercel              Hosted on a CDN and pointed at a remote dashboard via
 *                       VITE_API_ORIGIN. Root base, own output directory.
 *   preview             Snapshot-backed static demo for GitHub Pages. No
 *                       network layer at all.
 */
const TARGET = process.env.VITE_STATIC_DEMO === '1' ? 'preview' : (process.env.BUILD_TARGET ?? 'vps')

const VPS_OUT_DIR = fileURLToPath(new URL('../src/alpha_spy/dashboard/static', import.meta.url))

const OUTPUT = {
  vps: { base: '/static/', outDir: VPS_OUT_DIR },
  vercel: { base: '/', outDir: 'dist' },
  preview: { base: './', outDir: 'dist-preview' },
} as const

const { base, outDir } = OUTPUT[TARGET as keyof typeof OUTPUT] ?? OUTPUT.vps

// Dashboard default from alpha_spy/dashboard/config.py.
const BACKEND = process.env.ALPHA_SPY_DASHBOARD ?? 'http://127.0.0.1:8788'

export default defineConfig({
  base,
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir,
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // Charting is the heaviest dependency and is not needed to paint the
        // shell, so it is split out and loaded alongside the first render
        // rather than blocking it.
        manualChunks: {
          charts: ['echarts', 'lightweight-charts'],
          vendor: ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
})
