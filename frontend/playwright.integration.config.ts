import { defineConfig, devices } from '@playwright/test';

// A real FastAPI/Agent/tools/SQLite/Chroma path with a deterministic Provider.
// It never uses the normal application database or remote model credentials.
export default defineConfig({
  testDir: './tests/integration', workers: 1, retries: 0,
  outputDir: '../work/react-migration/integration-results',
  reporter: [['list'], ['json', { outputFile: '../work/react-migration/integration.json' }]],
  use: { baseURL: 'http://127.0.0.1:5174', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'real-http-chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: '..\\.venv\\Scripts\\python.exe ..\\scripts\\react_smoke_api.py --port 8010',
      url: 'http://127.0.0.1:8010/api/health', reuseExistingServer: true, timeout: 60_000,
    },
    {
      command: 'npm run dev -- --port 5174', url: 'http://127.0.0.1:5174',
      env: { VITE_API_BASE_URL: 'http://127.0.0.1:8010' },
      reuseExistingServer: false, timeout: 30_000,
    },
  ],
});
