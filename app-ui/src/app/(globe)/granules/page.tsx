"use client";

/** (globe)/granules — the Granule Explorer (§7.8): full-bleed, dark "instrument" theme scoped
 *  to this route. Suspense boundary required by useSearchParams. The maplibre chunk loads only
 *  on the (globe) routes; /workbench, /runs/*, /verdicts/* never see it. */
import { Suspense } from "react";
import ExplorerApp from "@/components/globe/explorer/ExplorerApp";

export default function GranulesPage() {
  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 flex items-center justify-center bg-[#0e1116]">
          <p className="text-xs text-[#8a837b]">loading the granule explorer…</p>
        </div>
      }
    >
      <ExplorerApp />
    </Suspense>
  );
}
