import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PROXY_TARGET = process.env.VITE_API_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": API_PROXY_TARGET,
      "/demo/reset-and-enter": API_PROXY_TARGET,
      "/demo/share": API_PROXY_TARGET
    }
  }
});
