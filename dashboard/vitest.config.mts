/**
 * Unit tests for the parts of the dashboard that are logic rather than layout —
 * the bus bridge, the wire translation, the ROI scorecard.
 *
 * `jsdom` because the durable log persists to localStorage, and a fake of it
 * would be testing our own fake. No React/component testing here: these are the
 * pieces whose failures are silent, which is why they are the ones with tests.
 */
import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
