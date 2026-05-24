/**
 * Vitest config — runs api/devices.ts tests against the REAL admin
 * gateway (no mocks, no msw, no fixtures).
 *
 * Tests under web/tests/ expect ``http://127.0.0.1:9000`` to be reachable;
 * if admin isn't running, each test self-skips. Mirrors the Python
 * fullstack tests' "skip if NATS unreachable" pattern — no spurious
 * red builds, but also no green-on-fakes.
 */
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // happy-dom gives us a window/document shim — required because the
    // shared axios client imports element-plus' ElMessage, which calls
    // ``document.createElement`` at module load. Plain node env would
    // crash on that import.
    //
    // The default happy-dom URL is ``about:blank``, which makes EVERY
    // outbound request cross-origin and trips CORS. We pin the URL to
    // ``http://127.0.0.1:9001`` (vite dev origin — admin's services.yaml
    // already CORS-allows this origin) so requests to admin :9000 pass
    // preflight against admin's real CORS config. No mocks, no
    // monkey-patching axios.
    environment: 'happy-dom',
    environmentOptions: {
      happyDOM: {
        url: 'http://127.0.0.1:9001/',
      },
    },
    include: ['tests/**/*.test.ts'],
    // Slower CI runs may take a beat to spin up; 10s per test is plenty
    // for localhost round-trips and well below pytest-asyncio's default.
    testTimeout: 10_000,
  },
})
