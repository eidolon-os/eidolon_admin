import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const apiHost = process.env.EIDOLON_ADMIN_API_HOST?.trim() || '127.0.0.1'
const apiPort = process.env.EIDOLON_ADMIN_API_PORT?.trim() || '9000'

if (!/^[A-Za-z0-9.-]+$/.test(apiHost)) {
  throw new Error('EIDOLON_ADMIN_API_HOST must be an IPv4 address or hostname')
}
if (!/^\d{1,5}$/.test(apiPort) || Number(apiPort) < 1 || Number(apiPort) > 65535) {
  throw new Error('EIDOLON_ADMIN_API_PORT must be between 1 and 65535')
}

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
        // The Ops host profile owns the API listener. Keeping the Vite proxy on
        // the same environment contract prevents a source-run UI from silently
        // talking to a different legacy Admin process.
        target: `http://${apiHost}:${apiPort}`,
        changeOrigin: false,
        ws: false,
      },
    },
  },
})
