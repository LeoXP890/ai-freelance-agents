import { defineConfig } from "@playwright/test";

// Regressão visual e de acessibilidade do escritório (QA-004 / OFF-007) contra o stack em execução (compose).
// WebGL por software (SwiftShader): roda sem GPU, inclusive no CI.
export default defineConfig({
  testDir: "e2e",
  // e2e/capture/: capturas de revisão (gravam imagens em docs/ e criam um proprietário de teste) — só sob demanda:
  //   AIC_CAPTURE=1 npx playwright test e2e/capture
  testIgnore: process.env.AIC_CAPTURE ? [] : ["capture/**"],
  timeout: 60_000,
  workers: 1,
  reporter: [["list"], ["junit", { outputFile: "../../progress/test-reports/web-e2e-junit.xml" }]],
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}-{platform}{ext}",
  // No CI (Linux) a primeira execução grava a referência da plataforma; depois compara.
  updateSnapshots: process.env.CI ? "missing" : "none",
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" } },
  use: {
    baseURL: process.env.AIC_E2E_BASE ?? "http://localhost:8080",
    viewport: { width: 1440, height: 900 },
    reducedMotion: "reduce",
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
