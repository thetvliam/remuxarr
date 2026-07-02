import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir:    "dist",
    emptyOutDir: true,
  },
  server: {
    // Dev server proxies /api and /ws to the FastAPI backend
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws":  { target: "ws://localhost:8000",   changeOrigin: true, ws: true },
    },
  },
});
