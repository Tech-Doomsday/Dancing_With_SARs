"use client";

/** /background/preprocessing (§5) — the P01…P13 chain view for a run or a cached bundle.
 *  Deep links: ?run=<id> or ?bundle=<id> open that selection. */
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import PreprocessView from "@/components/background/PreprocessView";

function Inner() {
  const q = useSearchParams();
  return <PreprocessView initialRun={q.get("run") ?? undefined} initialBundle={q.get("bundle") ?? undefined} />;
}

export default function PreprocessingPage() {
  return (
    <Suspense fallback={<p className="p-4 text-xs text-[#8a837b]">loading…</p>}>
      <Inner />
    </Suspense>
  );
}
