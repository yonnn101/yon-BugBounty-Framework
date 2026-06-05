import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/** Local `npm run dev` only: FastAPI lives under `/api` on the backend (see main.py). */
const api = process.env.VITE_PROXY_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: api, changeOrigin: true },
      "/health": { target: api, changeOrigin: true },
      "/docs": { target: api, changeOrigin: true },
      "/redoc": { target: api, changeOrigin: true },
      "/openapi.json": { target: api, changeOrigin: true },
    },
  },
});
