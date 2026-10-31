"use client";

/** /background → /background/overview. (The top-level Background tab in AppNav is separate on
 *  purpose: it links to Data retrieval carrying the Map's box.) Gating happens per API call (the engine answers 401/503); the layout hosts the
 *  token prompt. */
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function BackgroundIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/background/overview");
  }, [router]);
  return (
    <p className="p-6 text-xs text-[#8b949e]" role="status">
      redirecting to Overview…
    </p>
  );
}
