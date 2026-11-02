"use client";

/** The numbers the engine really uses when a request leaves them out (GET /api/settings/defaults).
 *  One source: config/common.yaml + config/retrieval.yaml (+ env). The UI never carries a copy of these. */
import { useEffect, useState } from "react";
import { engineJson } from "@/lib/engineFetch";

export interface EngineDefaults {
  lookback_days: number;
  max_gcov_epochs: number;
  max_gunw_pairs: number;
  poll_lookback_days: number;
  poll_products: string[];
  products: string[];
  tile_cell_m: number;
  earthquake: Record<string, number | string | boolean>;
  sources: Record<string, string>;
}

let cached: Promise<EngineDefaults> | null = null;

/** Fetched once per page load; null while loading or when the engine does not answer. */
export function useEngineDefaults(): EngineDefaults | null {
  const [d, setD] = useState<EngineDefaults | null>(null);
  useEffect(() => {
    let live = true;
    cached ??= engineJson<EngineDefaults>("/api/settings/defaults");
    cached.then((v) => live && setD(v)).catch(() => { cached = null; });
    return () => { live = false; };
  }, []);
  return d;
}
