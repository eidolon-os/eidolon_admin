import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // Explicit IPv4 bind: Vite 5 defaults to 'localhost' which on recent
    // node/macOS resolves to ::1 only, so http://127.0.0.1:9001 silently
    // fails. Binding 127.0.0.1 keeps the URL we tell users about working.
    host: '127.0.0.1',
    port: 9001,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9000',
        changeOrigin: false,
        ws: false,
      },
    },
  },
})
