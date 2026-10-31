"use client";

/** ExplainLine — the engine's plain words for a layer: what it is, its unit, how to read the picture.
 *  The copy lives in the ENGINE (config/explain.yaml, GET /api/background/explain); this component only
 *  displays it. Unknown fields render "—". One shared fetch for the whole tab (lib/explainCache). */
import { useEffect, useState } from "react";
import { GateError } from "@/lib/bgApi";
import { dash, loadExplain } from "@/lib/explainCache";
import type { ExplainPayload } from "@/lib/types";
import { useGateFail, useGateRetryNonce } from "./BackgroundGate";

export function useExplain(): { data: ExplainPayload | null; error: string | null } {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  const [data, setData] = useState<ExplainPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadExplain()
      .then((d) => live && (setData(d), setError(null)))
      .catch((err) => {
        if (!live) return;
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => { live = false; };
  }, [gateFail, retryNonce]);
  return { data, error };
}

export default function ExplainLine({ role, whatItIs, compact = false }: {
  /** the layer's role from the engine inventory (null = unknown) */
  role?: string | null;
  /** the engine's own sentence when the payload already carries it (the inventory does) */
  whatItIs?: string | null;
  compact?: boolean;
}) {
  const { data, error } = useExplain();
  const row = role && data ? data.layers[role] : undefined;
  const what = whatItIs ?? row?.what_it_is ?? null;
  if (error) return <p className="text-[10px] text-[#e8a087]">explanation unavailable: {error}</p>;
  return (
    <div className="text-[10px] leading-relaxed text-[#8b949e]" data-testid="explain-line">
      <p>{dash(what)}</p>
      {!compact && (
        <p>
          unit <span className="font-mono text-[#cfd6dc]">{dash(row?.unit)}</span> · {dash(row?.how_to_read)}
        </p>
      )}
    </div>
  );
}
