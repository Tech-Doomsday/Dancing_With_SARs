"use client";

/** /background/pipelines/[name] (§6) — one pipeline's Input / Process / Output for one run
 *  (?run=…, or picked in the view). */
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { PipelineDetailView } from "@/components/background/PipelinesView";

function Detail() {
  const params = useParams<{ name: string }>();
  const search = useSearchParams();
  const name = decodeURIComponent(Array.isArray(params.name) ? params.name[0] : params.name ?? "");
  return <PipelineDetailView name={name} initialRun={search.get("run") ?? undefined} />;
}

export default function PipelineDetailPage() {
  return (
    <Suspense fallback={<p className="p-4 text-xs text-[#8a837b]">loading…</p>}>
      <Detail />
    </Suspense>
  );
}
