import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

const API_PROXY_TARGET = process.env.VITE_API_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      react: path.resolve("./node_modules/react"),
      "react-dom": path.resolve("./node_modules/react-dom"),
      "react-router-dom": path.resolve("./node_modules/react-router-dom"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": API_PROXY_TARGET,
      "/demo/reset-and-enter": API_PROXY_TARGET,
      "/demo/share": API_PROXY_TARGET
    }
  }
});
