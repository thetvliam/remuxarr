import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir:    "dist",
    emptyOutDir: true,
  },
  // Vitest config lives here rather than in a separate vitest.config.js so
  // there is one place where aliases and plugins are declared — a second
  // config file is how test and build environments drift apart.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.js",
    include: ["src/**/*.test.{js,jsx}"],
    // The suite must stay fast enough to run on every commit, like the
    // backend's 3s one. Nothing here should touch the network or a timer
    // it does not control.
    testTimeout: 5000,
  },
  server: {
    // Dev server proxies /api and /ws to the FastAPI backend
    proxy: {
      "/api": { target: "http://localhost:9191", changeOrigin: true },
      "/ws":  { target: "ws://localhost:9191",   changeOrigin: true, ws: true },
    },
  },
});
