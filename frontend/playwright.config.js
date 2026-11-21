// เทสต์ end-to-end: เปิดหน้าเว็บจริงกับ backend จริง (ฐานข้อมูลที่ seed ข้อมูลสาธิต + ตัวบทสาธารณะ)
// ต้องเปิด API ไว้ที่ :8000 ก่อน (CI ทำให้ในงาน e2e) — Playwright เปิด vite dev ให้เอง
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    locale: 'th-TH',
    ...devices['Desktop Chrome'],
    viewport: { width: 1400, height: 900 },
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH, args: ['--no-sandbox'] } : {}
  },
  webServer: {
    command: 'npx vite --host 127.0.0.1 --port 5173 --strictPort',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000
  }
});
