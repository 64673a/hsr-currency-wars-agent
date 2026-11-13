import path from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// 开发时把接口和模板图标代理到后端(uv run cwagent serve,默认 8770 端口)
const backend = process.env.CWAGENT_BACKEND ?? 'http://127.0.0.1:8770';

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: { proxy: { '/api': backend, '/templates': backend } },
  build: { outDir: 'dist', emptyOutDir: true },
});
