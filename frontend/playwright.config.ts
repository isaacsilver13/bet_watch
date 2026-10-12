import { defineConfig, devices } from "@playwright/test";

const python = process.platform === "win32"
  ? "\"../.venv/Scripts/python.exe\""
  : "python";

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    launchOptions: process.platform === "win32"
      ? { executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe" }
      : undefined,
  },
  webServer: [
    {
      command: `${python} -m uvicorn browser_fixture:app --app-dir ../backend --host 127.0.0.1 --port 8000`,
      port: 8000,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        PROVIDER_MODE: "fixture",
        ODDS_PROVIDER: "fixture",
        THE_ODDS_API_KEY: "",
        KALSHI_ENABLED: "true",
        PERSISTENCE_ENABLED: "false",
        ADMIN_REFRESH_SECRET: "",
      },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4173",
      port: 4173,
      reuseExistingServer: !process.env.CI,
    },
  ],
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true } },
  ],
});
