import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  use: {
    browserName: 'chromium',
    trace: 'retain-on-failure',
    timezoneId: 'Asia/Tokyo',
  },
})
