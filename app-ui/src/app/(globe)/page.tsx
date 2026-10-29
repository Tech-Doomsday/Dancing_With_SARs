"use client";

/** (globe) route — the full-bleed landing experience (§7.1). Suspense boundary required by
 *  useSearchParams. The maplibre chunk loads ONLY here; /workbench, /runs/*, /verdicts/* never
 *  see it (route groups keep those under the site layout). */
import { Suspense } from "react";
import GlobeApp from "@/components/globe/GlobeApp";

export default function GlobePage() {
  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 flex items-center justify-center bg-[#0b0d12]">
          <p className="text-xs text-[#8a837b]">loading the globe…</p>
        </div>
      }
    >
      <GlobeApp />
    </Suspense>
  );
}
