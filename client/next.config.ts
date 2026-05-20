import type { NextConfig } from "next";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(__dirname),
  // next lint hangs locally with ESLint 9 + legacy plugin stack; typecheck still runs.
  eslint: {
    ignoreDuringBuilds: true,
    dirs: ["app", "components", "lib"],
  },
};

export default nextConfig;
