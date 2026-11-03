"use client";

/** background/retrieval — the Granule Explorer (UI-build-2 §4), moved from /granules (§1):
 *  same behaviour, new base path, plus GUNW/GCOV group rendering, detail tabs and the Compare
 *  view. Suspense boundary required by useSearchParams. */
import { Suspense } from "react";
import ExplorerApp from "@/components/globe/explorer/ExplorerApp";

export default function RetrievalPage() {
  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 flex items-center justify-center bg-[#0e1116]">
          <p className="text-xs text-[#8a837b]">loading data retrieval…</p>
        </div>
      }
    >
      <ExplorerApp />
    </Suspense>
  );
}
