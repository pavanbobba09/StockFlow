import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/demo": "http://localhost:8000",
      "/agents": "http://localhost:8000",
      "/live": "http://localhost:8000",
      "/metrics": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
