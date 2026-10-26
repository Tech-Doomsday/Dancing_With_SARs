import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: false,
  // Allow running `next dev` behind sandboxed/preview gateways (harmless
  // elsewhere — it only permits dev-server cross-origin requests).
  allowedDevOrigins: ["*.space-z.ai"],
};

export default nextConfig;
