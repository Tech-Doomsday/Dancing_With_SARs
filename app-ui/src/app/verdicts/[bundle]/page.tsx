"use client";

import { use } from "react";
import CachedView from "@/components/CachedView";

function safeDecode(v: string): string {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

export default function CachedVerdictPage({ params }: { params: Promise<{ bundle: string }> }) {
  const { bundle } = use(params);
  return (
    <div className="space-y-4">
      <a href="/" className="text-xs text-[#2c5f58] hover:underline">
        ← all verdicts
      </a>
      <CachedView bundleId={safeDecode(bundle)} />
    </div>
  );
}
