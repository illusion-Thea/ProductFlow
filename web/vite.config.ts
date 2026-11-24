import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { verificationEnvironment } from "./verification.config.mjs";

export default defineConfig(({ mode }) => {
  const isolatedEnv = verificationEnvironment();
  const verification = isolatedEnv !== null;
  const env = isolatedEnv ?? loadEnv(mode, ".", "");
  const devPort = Number(env.WEB_PORT || 29283);
  const previewPort = Number(env.WEB_PORT || 29281);
  const devProxyTarget = env.VITE_DEV_PROXY_TARGET || "http://127.0.0.1:29282";
  const allowedHosts = env.WEB_ALLOWED_HOSTS
    ? env.WEB_ALLOWED_HOSTS.split(",").map((host) => host.trim()).filter(Boolean)
    : ["draw.devbin.de"];

  return {
    envDir: verification ? false : undefined,
    cacheDir: verification ? env.M101_WEB_CACHE : undefined,
    define: verification ? { "import.meta.env.VITE_VERIFICATION_ENVIRONMENT_ID": JSON.stringify(env.VITE_VERIFICATION_ENVIRONMENT_ID) } : {},
    build: verification ? { outDir: env.M101_WEB_DIST, emptyOutDir: false } : undefined,
    plugins: [react(), tailwindcss()],
    server: {
      port: devPort,
      strictPort: true,
      host: verification ? "127.0.0.1" : "0.0.0.0",
      allowedHosts,
      proxy: {
        "/api": {
          target: devProxyTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: previewPort,
      strictPort: true,
      host: verification ? "127.0.0.1" : "0.0.0.0",
      allowedHosts,
    },
  };
});
