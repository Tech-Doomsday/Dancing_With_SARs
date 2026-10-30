import type { NextConfig } from "next";

/** UI-build-2 §1 compatibility redirects — the old base paths keep working with their query
 *  strings. `has` conditions keep the Landing page at "/" while ANY legacy globe query goes to
 *  /map. Next.js forwards the original query string when the destination has none of its own. */
const LEGACY_MAP_KEYS = ["lat", "lon", "z", "tile", "bundle", "pipeline", "layer", "scan", "panel"];

const legacyMapRedirects = LEGACY_MAP_KEYS.map((key) => ({
  source: "/",
  has: [{ type: "query" as const, key }],
  destination: "/map",
  permanent: false,
}));

const nextConfig: NextConfig = {
  /* The engine API base URL (FastAPI). Overridable via env. */
  env: {
    NEXT_PUBLIC_ENGINE_API: process.env.NEXT_PUBLIC_ENGINE_API ?? "http://127.0.0.1:8900",
  },
  async redirects() {
    return [
      ...legacyMapRedirects,
      { source: "/granules", destination: "/background/retrieval", permanent: false },
      { source: "/workbench", destination: "/background/pipelines", permanent: false },
    ];
  },
};

export default nextConfig;
