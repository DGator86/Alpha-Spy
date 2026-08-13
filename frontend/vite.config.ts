import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// The default build lands directly in the FastAPI static directory. The wheel
// therefore ships a pre-built workstation and a VPS install never needs Node.
const OUT_DIR = fileURLToPath(new URL('../src/alpha_spy/dashboard/static', import.meta.url))

// The static preview is the same application built against a committed snapshot
// and published to GitHub Pages, so it uses relative asset paths and its own
// output directory rather than overwriting the wheel's bundle.
const STATIC_DEMO = process.env.VITE_STATIC_DEMO === '1'

// Dashboard default from alpha_spy/dashboard/config.py.
const BACKEND = process.env.ALPHA_SPY_DASHBOARD ?? 'http://127.0.0.1:8788'

export default defineConfig({
  base: STATIC_DEMO ? './' : '/static/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: STATIC_DEMO ? 'dist-preview' : OUT_DIR,
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
