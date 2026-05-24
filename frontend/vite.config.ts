import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import pkg from './package.json' with { type: 'json' };

// Vite dev server proxies /api/* to whatever backend is reachable.
// Default points at the dev compose's LAN-bypass port (5065). Override with
// VITE_API_URL when iterating against the homelab production API.
const env = loadEnv('', process.cwd(), '');
const apiTarget = env.VITE_API_URL || 'http://localhost:5065';

export default defineConfig({
  plugins: [react()],
  // 1.1.3 — bake the web container's own version into the bundle at
  // build time, sourced from this package's own package.json. The
  // Settings page reads __URSA_WEB_VERSION__ client-side to populate
  // the web image-version chip without any API roundtrip. Eliminates
  // the operator's need to keep a display env var in sync with the
  // image tag.
  define: {
    __URSA_WEB_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/healthz': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
