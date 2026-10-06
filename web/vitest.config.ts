import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Unit tests for the frontend's pure logic — formatters, chaos-window
 * expansion, the graph layout, the API client's error contract.
 *
 * They live in `tests/`, one file per module under test, mirroring the
 * backend's `tests/test_<module>.py` layout — `src/core/lib` stays what
 * its name says it is, a folder of helpers.
 *
 * Deliberately node-environment and dependency-light: it does not render
 * React, so it needs no DOM shim and stays fast enough to run on every
 * save. End-to-end behaviour belongs in the Playwright suite; backend
 * behaviour belongs in pytest. This layer covers the logic that sits in
 * between, where the regressions it guards actually happened.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    reporters: "default",
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
