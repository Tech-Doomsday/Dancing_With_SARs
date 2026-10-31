"use client";

import { use, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BundleListItem } from "@/lib/types";
import RunView from "@/components/RunView";
import EventsPanel from "@/components/EventsPanel";

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [bounds, setBounds] = useState<[[number, number], [number, number]] | null>(null);
  const [center, setCenter] = useState<[number, number] | null>(null);

  useEffect(() => {
    // The run's bundle gives the map its geographic frame (metadata only).
    // The state is FLAT: {ok, id, status, results, ...} — no nested `run` key.
    api
      .run(id)
      .then((r) => {
        const bundleId = r.results?.[0]?.bundle_id;
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
          if (b?.center_lonlat) setCenter([b.center_lonlat[1], b.center_lonlat[0]]);
        });
      })
      .catch(() => {});
  }, [id]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="mono text-sm font-semibold text-[#8a837b]">run {id}</h1>
        <a href="/map" className="text-xs text-[#2c5f58] hover:underline">
          ← all bundles
        </a>
      </div>
      <RunView runId={id} bounds={bounds} />
      {center && <EventsPanel lon={center[1]} lat={center[0]} />}
    </div>
  );
}
