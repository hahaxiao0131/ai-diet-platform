import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const proxyTarget = env.VITE_DEV_API_PROXY_TARGET?.trim() || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      port: 5174,
      strictPort: true,
      host: "127.0.0.1",
      proxy: {
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
