import type { NextConfig } from "next";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function backendUrl(): string {
  const raw =
    process.env.BACKEND_URL ??
    (process.env.NODE_ENV === "development"
      ? process.env.NEXT_PUBLIC_API_URL
      : undefined) ??
    "http://localhost:8000";
  return raw.replace(/\/$/, "");
}

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(__dirname),
  // Browser calls same-origin /api/*; Next proxies to BACKEND_URL (runtime on Railway).
  async rewrites() {
    const target = backendUrl();
    return [
      {
        source: "/api/:path*",
        destination: `${target}/api/:path*`,
      },
      {
        source: "/health",
        destination: `${target}/health`,
      },
    ];
  },
  // next lint hangs locally with ESLint 9 + legacy plugin stack; typecheck still runs.
  eslint: {
    ignoreDuringBuilds: true,
    dirs: ["app", "components", "lib"],
  },
};

export default nextConfig;
