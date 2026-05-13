import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Vite dev server proxies /api/* to whatever backend is reachable.
// Default points at the dev compose's LAN-bypass port (5065). Override with
// VITE_API_URL when iterating against the homelab production API.
const env = loadEnv('', process.cwd(), '');
const apiTarget = env.VITE_API_URL || 'http://localhost:5065';

export default defineConfig({
  plugins: [react()],
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
