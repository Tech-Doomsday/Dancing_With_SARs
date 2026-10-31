"use client";

/** /background/pipelines (§6) — every pipeline from /api/pipelines, honestly labelled.
 *  Deep link: ?run=<id> preselects that run. */
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import PipelinesView from "@/components/background/PipelinesView";

function Inner() {
  return <PipelinesView initialRun={useSearchParams().get("run") ?? undefined} />;
}

export default function PipelinesPage() {
  return (
    <Suspense fallback={<p className="p-4 text-xs text-[#8a837b]">loading…</p>}>
      <Inner />
    </Suspense>
  );
}
