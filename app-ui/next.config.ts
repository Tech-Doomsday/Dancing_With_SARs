import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* The engine API base URL (FastAPI). Overridable via env. */
  env: {
    NEXT_PUBLIC_ENGINE_API: process.env.NEXT_PUBLIC_ENGINE_API ?? "http://127.0.0.1:8900",
  },
};

export default nextConfig;
