import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// dev: ส่ง /api ต่อไปที่ FastAPI (ใน docker-compose ใช้ API_URL=http://api:8000)
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { '/api': { target: process.env.API_URL || 'http://localhost:8000', changeOrigin: true } }
  }
});
