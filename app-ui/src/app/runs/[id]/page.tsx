"use client";

import { use, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BundleListItem } from "@/lib/types";
import RunView from "@/components/RunView";

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [bounds, setBounds] = useState<[[number, number], [number, number]] | null>(null);

  useEffect(() => {
    // The run's bundle gives the map its geographic frame (metadata only).
    api.run(id)
      .then((r) => {
        const bundleId = r.run.results[0]?.bundle_id;
        if (!bundleId) return;
        return api.bundles().then((all) => {
          const b: BundleListItem | undefined = all.bundles.find((x) => x.bundle_id === bundleId);
          if (b?.bbox_lonlat) {
            const [w, s, e, n] = b.bbox_lonlat;
            setBounds([
              [s, w],
              [n, e],
            ]);
          }
        });
      })
      .catch(() => {});
  }, [id]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="mono text-sm font-semibold text-[#8a837b]">run {id}</h1>
        <a href="/" className="text-xs text-[#2c5f58] hover:underline">
          ← all bundles
        </a>
      </div>
      <RunView runId={id} bounds={bounds} />
    </div>
  );
}
