import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// VITE_BASE lets GitHub Pages serve the app under /<repo>/.
export default defineConfig({
  base: process.env.VITE_BASE ?? '/',
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: { sourcemap: true, chunkSizeWarningLimit: 1200 },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
