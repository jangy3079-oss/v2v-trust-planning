import { defineConfig } from 'vite';

// 서버 모드(?source=server)는 /api 를 목업(또는 실제) 서버로 프록시한다 → 브라우저는 같은 출처로 요청 (CORS 불필요)
const API_TARGET = process.env['DEFY_API_TARGET'] ?? 'http://localhost:8787';
const proxy = { '/api': { target: API_TARGET, ws: true, changeOrigin: true } };

export default defineConfig({
  base: './',
  build: { target: 'es2022' }, // main.ts 의 top-level await (서버 모드 DemoConfig 로드)
  server: { proxy },
  preview: { proxy },
});
