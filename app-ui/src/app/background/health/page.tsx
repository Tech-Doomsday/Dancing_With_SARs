"use client";

/** /background/health — environment and file checks. PLACEHOLDER: the engine route that feeds it
 *  (GET /api/background/health) is not built yet, so this page says so instead of faking nodes. */
export default function HealthPage() {
  return (
    <div className="p-4 text-xs" aria-label="health">
      <h1 className="text-sm font-semibold text-[#f2efe9]">Health</h1>
      <p role="status" className="mt-2 max-w-2xl text-[#8b949e]">
        Not built yet: this page needs the engine&apos;s health route, which does not exist. Use the
        other sections meanwhile.
      </p>
    </div>
  );
}
