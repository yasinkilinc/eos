import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev:  vite serves on :5173 and proxies /api -> the FastAPI server on :8000.
// Prod: `npm run build` emits to ../static, served by FastAPI at /static +
// index.html at /. Base "/static/" makes asset URLs resolve correctly.
export default defineConfig({
  plugins: [react()],
  base: "/static/",
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
});
