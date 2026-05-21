import type { NextConfig } from "next";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const backendUrl = (
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000"
).replace(/\/$/, "");

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(__dirname),
  // Browser calls same-origin /api/*; Next proxies to BACKEND_URL (runtime on Railway).
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
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
