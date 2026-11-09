import { defineConfig } from '@playwright/test';

// End-to-end tests drive the real Svelte front end in Chromium and WebKit (the
// engine behind the macOS app) against the Vite dev server. A scripted fake of
// the Tauri command bridge stands in for the Rust host, see e2e/fake-host.ts.
const port = 1430;
const sizes = { '1024': { width: 1024, height: 700 }, '1440': { width: 1440, height: 900 } };
const engines = ['chromium', 'webkit'] as const;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 30_000,
  expect: {
    timeout: 7_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.002, animations: 'disabled', caret: 'hide' },
  },
  // One baseline per project and platform: font rendering differs between them.
  snapshotPathTemplate: '{testDir}/__screenshots__/{projectName}/{arg}-{platform}{ext}',
  use: {
    baseURL: `http://localhost:${port}`,
    colorScheme: 'dark',
    locale: 'en-US',
    timezoneId: 'Europe/Zurich',
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
  },
  projects: engines.flatMap((browserName) =>
    Object.entries(sizes).map(([name, viewport]) => ({
      name: `${browserName}-${name}`,
      use: { browserName, viewport, deviceScaleFactor: 1 },
    })),
  ),
  webServer: {
    command: `npx vite --port ${port} --strictPort --host localhost`,
    url: `http://localhost:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
