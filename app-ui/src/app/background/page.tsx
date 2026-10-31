"use client";

/** /background → /background/retrieval (§1). The Background tab's default section is Data
 *  retrieval. Gating happens per API call (the engine answers 401/503); the layout hosts the
 *  token prompt. */
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function BackgroundIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/background/retrieval");
  }, [router]);
  return (
    <p className="p-6 text-xs text-[#8b949e]" role="status">
      redirecting to Data retrieval…
    </p>
  );
}
