"use client";

/** (globe)/map — the globe app, now at /map (UI-build-2 §3: "Move only"). Suspense boundary
 *  required by useSearchParams. The maplibre chunk loads ONLY on the (globe) routes; the
 *  Landing (/), /background/retrieval's own chrome and the (site) pages never see it.
 *  Behaviour is unchanged from the globe task build; only the base path moved. */
import { Suspense } from "react";
import GlobeApp from "@/components/globe/GlobeApp";

export default function MapPage() {
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
