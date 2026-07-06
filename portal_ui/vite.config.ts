import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forward all /portal/* requests to Portal_API during local development.
      // In production, nginx handles this reverse-proxy transparently.
      '/portal': {
        target: 'http://127.0.0.1:8084',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
  },
})
