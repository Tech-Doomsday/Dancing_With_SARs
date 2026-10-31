"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";

/** Redo = run the engine again on the same bundle with its DEFAULT parameters. The engine then
 *  overwrites the cached verdict (and only if it produced a real verdict — a failed redo never
 *  replaces a good one). Two-step, because it replaces what everyone else sees. */
export default function RedoButton({
  bundleId,
  pipelines,
  unavailable,
}: {
  bundleId: string;
  pipelines: string[];
  /** why redo cannot be offered right now (engine offline, bundle not on this engine, …) */
  unavailable?: string | null;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      const { run_id } = await api.startRun(bundleId, pipelines);
      router.push(`/runs/${run_id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
      setConfirming(false);
    }
  };

  if (unavailable) {
    return (
      <div className="text-right">
        <button
          disabled
          className="inline-flex items-center gap-2 rounded-md border border-[#e7e2da] px-3 py-1.5 text-xs font-medium text-[#8a837b] opacity-60"
        >
          <RotateCcw size={13} /> Redo verdict
        </button>
        <p className="mt-1 max-w-[16rem] text-[11px] text-[#8a837b]">{unavailable}</p>
      </div>
    );
  }

  return (
    <div className="text-right">
      {!confirming ? (
        <button
          onClick={() => setConfirming(true)}
          className="inline-flex items-center gap-2 rounded-md border border-[#2c5f58] px-3 py-1.5 text-xs font-medium text-[#2c5f58] transition-colors hover:bg-[#eff5f3]"
        >
          <RotateCcw size={13} /> Redo verdict
        </button>
      ) : (
        <div className="inline-flex flex-col items-end gap-1.5">
          <span className="max-w-[18rem] text-[11px] text-[#4a443c]">
            Re-run the engine on this bundle and replace the cached verdict?
          </span>
          <span className="flex gap-2">
            <button
              onClick={go}
              disabled={busy}
              className="rounded-md bg-[#2c5f58] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {busy ? "Starting…" : "Yes, redo"}
            </button>
            <button
              onClick={() => setConfirming(false)}
              disabled={busy}
              className="rounded-md border border-[#e7e2da] px-3 py-1.5 text-xs text-[#4a443c]"
            >
              Cancel
            </button>
          </span>
        </div>
      )}
      {err && (
        <p role="alert" className="mono mt-2 rounded border border-[#9e3b2c] bg-[#fbf3ef] px-2 py-1 text-[11px] text-[#9e3b2c]">
          {err}
        </p>
      )}
    </div>
  );
}
