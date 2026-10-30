"use client";

/** PreprocessView (§5) — what the common chain P01…P13 did for a run or a cached bundle, step by
 *  step: name, what it read, what it wrote, flags, assumptions, timing, and the arrays it
 *  produced as images (hover probe, click to enlarge). Plus the SceneBundle summary and the
 *  hand-off table (Registry.plan + declared-input presence). A run with no recorded trace shows
 *  the honest empty state. Source: GET /api/background/runs/{id}/preprocessing (+ bundle_id). */
import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Maximize2, X } from "lucide-react";
import { api } from "@/lib/api";
import { bg } from "@/lib/bg";
import { bgGet, GateError } from "@/lib/bgApi";
import { useGateFail, useGateRetryNonce } from "./BackgroundGate";
import type { BundleListItem, PreprocessingResponse, RunSummary, TraceArrayMeta } from "@/lib/types";

type Selection = { kind: "run"; id: string; label: string } | { kind: "bundle"; id: string; label: string } | null;

export default function PreprocessView() {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [bundles, setBundles] = useState<BundleListItem[]>([]);
  const [sel, setSel] = useState<Selection>(null);
  const [payload, setPayload] = useState<PreprocessingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api.runs().then((r) => setRuns(r.runs)).catch(() => setRuns([]));
    bgGet<{ ok: true; bundles: BundleListItem[] }>("/api/bundles", ctrl.signal)
      .then((r) => setBundles(r.bundles))
      .catch(() => setBundles([]));
    return () => ctrl.abort();
  }, []);

  const load = useCallback((selection: Selection) => {
    setSel(selection);
    setPayload(null);
    setError(null);
    if (!selection) return;
    setLoading(true);
    const ctrl = new AbortController();
    bg.preprocessing(selection.kind === "run" ? selection.id : "__bundle__",
      selection.kind === "bundle" ? selection.id : undefined, ctrl.signal)
      .then((r) => { setPayload(r); setLoading(false); })
      .catch((err) => {
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, [gateFail]);
  // a saved token re-triggers the last load (the gate prompt's retry)
  useEffect(() => {
    if (retryNonce > 0) load(sel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryNonce]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 text-xs" aria-label="preprocessing">
      <h1 className="text-sm font-semibold text-[#f2efe9]">Preprocessing — the P01…P13 chain, step by step</h1>
      <p className="mt-1 max-w-3xl text-[11px] leading-relaxed text-[#8b949e]">
        What the common chain did for one run or one cached bundle: each step&apos;s name, what it
        read, what it wrote, the flags and assumptions it recorded, how long it took, and the
        arrays it produced as images (hover to probe a pixel, click to enlarge). Everything below
        is the engine&apos;s own record — nothing is reconstructed.
      </p>

      {/* selector: recent runs + cached bundles */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-[11px] text-[#8b949e]">
          recent runs
          <select
            value={sel?.kind === "run" ? sel.id : ""}
            onChange={(e) => { const r = runs.find((x) => x.id === e.target.value); if (r) load({ kind: "run", id: r.id, label: r.id }); }}
            className="max-w-[240px] rounded border border-[#333b47] bg-[#0e1116] px-1.5 py-1 font-mono text-[11px] text-[#e6eaee]"
            aria-label="pick a run"
          >
            <option value="">— pick a run —</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>{r.id} · {r.status}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-[11px] text-[#8b949e]">
          cached bundles
          <select
            value={sel?.kind === "bundle" ? sel.id : ""}
            onChange={(e) => { const b = bundles.find((x) => x.bundle_id === e.target.value); if (b) load({ kind: "bundle", id: b.bundle_id, label: b.bundle_id }); }}
            className="max-w-[240px] rounded border border-[#333b47] bg-[#0e1116] px-1.5 py-1 font-mono text-[11px] text-[#e6eaee]"
            aria-label="pick a cached bundle"
          >
            <option value="">— pick a cached bundle —</option>
            {bundles.map((b) => (
              <option key={b.bundle_id} value={b.bundle_id}>{b.tile ?? "tile —"} · {b.bundle_id.slice(0, 18)}…</option>
            ))}
          </select>
        </label>
        {loading && (
          <span className="flex items-center gap-1.5 text-[11px] text-[#8b949e]" role="status" aria-live="polite">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-[#3fb6c9] border-t-transparent" aria-hidden />
            loading the trace…
          </span>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-3 max-w-3xl border border-[#4a2f24] bg-[#2a1712] px-2 py-1.5 text-[11px] text-[#e8a087]">
          {error}
        </p>
      )}

      {!sel && !error && (
        <p className="mt-4 text-[11px] text-[#8b949e]" role="status">
          pick a run (recent runs are listed first) or a cached bundle to see its chain record.
        </p>
      )}

      {payload?.empty_reason && (
        <div className="mt-4 max-w-3xl rounded-md border border-[#4a3a1f] bg-[#1d1810] p-3 text-[11px] leading-relaxed text-[#d9c98a]" role="status">
          {payload.empty_reason}
        </div>
      )}

      {payload?.trace && (
        <TraceView payload={payload} />
      )}
    </div>
  );
}

function TraceView({ payload }: { payload: PreprocessingResponse }) {
  const trace = payload.trace!;
  const [openStep, setOpenStep] = useState<string | null>(trace.steps[0]?.code ?? null);
  const [zoom, setZoom] = useState<{ runId: string | null; aid: string; meta: TraceArrayMeta } | null>(null);
  return (
    <div className="mt-3 space-y-3">
      <p className="text-[10px] text-[#5a636e]">
        run {payload.run.id ?? "(bundle)"} · tile {payload.run.tile ?? "—"} · status {payload.run.status ?? "—"}
        {trace.source && <> · {trace.source}</>}
      </p>

      {/* steps */}
      <ol className="space-y-1.5">
        {trace.steps.map((s) => {
          const open = openStep === s.code;
          return (
            <li key={s.code} className="rounded-md border border-[#262c36] bg-[#12161d]">
              <button
                type="button"
                onClick={() => setOpenStep(open ? null : s.code)}
                aria-expanded={open}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-2.5 py-1.5 text-left"
              >
                {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
                <span className="font-mono text-[11px] text-[#7fd8e6]">{s.code}</span>
                <span className="text-[11px] font-medium text-[#e6eaee]">{s.name}</span>
                <span className={`rounded border px-1.5 py-px text-[9px] uppercase ${
                  s.status === "ok" ? "border-[#2c5f58] text-[#8fd0b8]"
                  : s.status === "skipped" ? "border-[#333b47] text-[#8b949e]"
                  : "border-[#4a2f24] bg-[#2a1712] text-[#e8a087]"}`}>
                  {s.status}
                </span>
                <span className="font-mono text-[10px] text-[#8b949e]">{s.seconds.toFixed(2)} s</span>
                <span className="text-[10px] text-[#5a636e]">
                  wrote {s.arrays.length} array{s.arrays.length === 1 ? "" : "s"}
                </span>
              </button>
              {open && (
                <div className="border-t border-[#262c36] px-3 py-2 text-[10.5px]">
                  <p><span className="text-[#8b949e]">read:</span> {s.reads?.length ? s.reads.join(", ") : "— (always runs)"}</p>
                  <p><span className="text-[#8b949e]">wrote:</span> {s.arrays.length ? s.arrays.map((a) => a.label).join(", ") : "no arrays recorded"}</p>
                  {s.message && <p><span className="text-[#8b949e]">message:</span> {s.message}</p>}
                  {s.new_flags.length > 0 && (
                    <p><span className="text-[#8b949e]">flags:</span>{" "}
                      {s.new_flags.map((f, i) => (
                        <span key={i} className={`mr-1 rounded border px-1 py-px ${f.severity === "error" ? "border-[#4a2f24] text-[#e8a087]" : f.severity === "warning" ? "border-[#4a3a1f] text-[#e6c977]" : "border-[#333b47] text-[#8b949e]"}`}>
                          {f.code}: {f.message}
                        </span>
                      ))}
                    </p>
                  )}
                  {s.new_assumptions.length > 0 && (
                    <p><span className="text-[#8b949e]">assumptions:</span> {s.new_assumptions.join(" · ")}</p>
                  )}
                  {/* arrays as images (hover probe, click to enlarge) */}
                  {s.arrays.length > 0 && (
                    <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                      {s.arrays.map((a) => (
                        <TraceArrayCard key={a.aid} meta={a} runId={payload.run.id} onZoom={setZoom} />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {/* SceneBundle summary */}
      {trace.bundle_summary && (
        <section className="rounded-md border border-[#262c36] bg-[#12161d] p-3">
          <h2 className="text-xs font-semibold text-[#f2efe9]">SceneBundle summary</h2>
          <BundleSummary b={trace.bundle_summary as Record<string, never>} />
        </section>
      )}

      {/* hand-off table */}
      {trace.handoff && (
        <section className="rounded-md border border-[#262c36] bg-[#12161d] p-3">
          <h2 className="text-xs font-semibold text-[#f2efe9]">
            Hand-off — what Registry.plan would do, and which declared inputs are present
          </h2>
          <p className="mt-1 text-[10px] text-[#8b949e]">
            tally: {Object.entries(trace.handoff.tally).map(([k, v]) => `${k}=${v}`).join(" · ") || "—"}
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[10px]">
              <thead className="text-[#8b949e]">
                <tr>
                  <th className="py-1 pr-2 font-normal">pipeline</th>
                  <th className="py-1 pr-2 font-normal">feed</th>
                  <th className="py-1 pr-2 font-normal">uses</th>
                  <th className="py-1 pr-2 font-normal">own fetch</th>
                  <th className="py-1 font-normal">routing</th>
                </tr>
              </thead>
              <tbody className="text-[#cfd6dc]">
                {trace.handoff.rows.map((r) => (
                  <tr key={r.name} className="border-t border-[#1c222b] align-top">
                    <td className="py-1 pr-2 font-mono">{r.name}{!r.implemented && <span className="ml-1 text-[#8b949e]">(not modelled yet)</span>}</td>
                    <td className="py-1 pr-2">{r.feed}</td>
                    <td className="py-1 pr-2">
                      {r.uses.map((u) => (
                        <span key={u.code} className={`mr-1 rounded border px-1 py-px ${u.state === "present" ? "border-[#2c5f58] text-[#8fd0b8]" : "border-[#4a3a1f] text-[#e6c977]"}`} title={u.why}>
                          {u.code} {u.state}
                        </span>
                      ))}
                    </td>
                    <td className="py-1 pr-2">
                      {r.own_fetch.map((u) => (
                        <span key={u.code} className={`mr-1 rounded border px-1 py-px ${u.state === "present" ? "border-[#2c5f58] text-[#8fd0b8]" : u.state === "no_producer" ? "border-[#333b47] text-[#8b949e]" : "border-[#4a3a1f] text-[#e6c977]"}`} title={u.why}>
                          {u.code} {u.state}
                        </span>
                      ))}
                    </td>
                    <td className="py-1 font-mono text-[9.5px] text-[#8b949e]">{r.routing ? JSON.stringify(r.routing) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* enlarge modal */}
      {zoom && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-6" role="dialog" aria-label="enlarged array" onClick={() => setZoom(null)}>
          <div className="max-h-full max-w-4xl overflow-auto rounded-lg border border-[#262c36] bg-[#0e1116] p-3" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="font-mono text-[11px] text-[#cfd6dc]">{zoom.meta.path}</p>
              <button type="button" onClick={() => setZoom(null)} aria-label="close" className="rounded p-1 text-[#8b949e] hover:text-[#e6eaee]">
                <X size={14} aria-hidden />
              </button>
            </div>
            <TraceArrayImage runId={zoom.runId} aid={zoom.aid} maxPx={640} />
            <p className="mt-2 text-[10px] text-[#8b949e]">
              {zoom.meta.label} · {zoom.meta.shape.join("×")} {zoom.meta.dtype} · {zoom.meta.group} (step {zoom.meta.step})
              {zoom.meta.note ? ` · ${zoom.meta.note}` : ""}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function TraceArrayCard({ meta, runId, onZoom }: {
  meta: TraceArrayMeta;
  runId: string | null;
  onZoom: (z: { runId: string | null; aid: string; meta: TraceArrayMeta }) => void;
}) {
  const gateFail = useGateFail();
  const [probe, setProbe] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!runId) { setFailed("array images exist only for traced runs"); return; }
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    (async () => {
      try {
        const { bgFetch } = await import("@/lib/bgApi");
        const res = await bgFetch(bg.traceArrayUrl(runId, meta.aid, { maxPx: 200 }), { signal: ctrl.signal });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error((body as { detail?: { error?: string } })?.detail?.error ?? `${res.status}`);
        }
        const blob = await res.blob();
        if (!ctrl.signal.aborted) setUrl(URL.createObjectURL(blob));
      } catch (err) {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setFailed(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => ctrl.abort();
  }, [runId, meta.aid, gateFail]);

  const hover = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!runId || !url) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    bg.traceProbe(runId, meta.aid, fx, fy)
      .then((r) => setProbe(r.finite
        ? `${r.value} @ ${r.lat != null ? r.lat.toFixed(4) : "—"}, ${r.lon != null ? r.lon.toFixed(4) : "—"}`
        : "NaN (no data here)"))
      .catch(() => setProbe(null));
  };

  return (
    <figure
      className="overflow-hidden rounded border border-[#262c36] bg-[#0e1116]"
      onMouseMove={hover}
      onMouseLeave={() => setProbe(null)}
    >
      <button
        type="button"
        className="relative block h-28 w-full"
        onClick={() => onZoom({ runId, aid: meta.aid, meta })}
        disabled={!url}
        aria-label={`enlarge ${meta.label}`}
      >
        {url && <img src={url} alt={`array ${meta.label}`} className="h-full w-full object-contain" draggable={false} />}
        {!url && !failed && <span className="flex h-full items-center justify-center text-[10px] text-[#8b949e]" role="status">rendering…</span>}
        {failed && <span className="flex h-full items-center justify-center px-1 text-center text-[9px] text-[#e8a087]">{failed}</span>}
        <Maximize2 size={11} aria-hidden className="absolute right-1 top-1 text-[#5a636e]" />
      </button>
      <figcaption className="border-t border-[#262c36] px-1.5 py-1 text-[9px] leading-tight text-[#8b949e]">
        <p className="truncate font-mono text-[#cfd6dc]" title={meta.path}>{meta.label}</p>
        <p>{meta.shape.join("×")} {meta.dtype}</p>
        {probe && <p className="text-[#e6c977]">{probe}</p>}
      </figcaption>
    </figure>
  );
}

function TraceArrayImage({ runId, aid, maxPx }: { runId: string | null; aid: string; maxPx: number }) {
  const gateFail = useGateFail();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    if (!runId) { setFailed("no traced run"); return; }
    const ctrl = new AbortController();
    (async () => {
      try {
        const { bgFetch } = await import("@/lib/bgApi");
        const res = await bgFetch(bg.traceArrayUrl(runId, aid, { maxPx }), { signal: ctrl.signal });
        if (!res.ok) throw new Error(`${res.status}`);
        setUrl(URL.createObjectURL(await res.blob()));
      } catch (err) {
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setFailed(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => ctrl.abort();
  }, [runId, aid, maxPx, gateFail]);
  if (failed) return <p role="alert" className="text-[11px] text-[#e8a087]">{failed}</p>;
  if (!url) return <p role="status" className="text-[11px] text-[#8b949e]">rendering…</p>;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={url} alt="trace array, enlarged" className="max-h-[70vh] w-full object-contain" />;
}

function BundleSummary({ b }: { b: Record<string, never> }) {
  const get = (k: string): unknown => (b as Record<string, unknown>)[k];
  const rows: [string, unknown][] = [
    ["bundle_id", get("bundle_id")],
    ["status", get("status")],
    ["epoch", get("epoch")],
    ["capabilities", Array.isArray(get("capabilities")) ? (get("capabilities") as string[]).join(", ") : get("capabilities")],
    ["depth", JSON.stringify(get("depth"))],
    ["created_at", (get("provenance") as { created_at?: string } | undefined)?.created_at],
  ];
  return (
    <dl className="mt-2">
      {rows.map(([k, v]) => (
        <div key={k} className="flex items-start justify-between gap-3 border-b border-[#1c222b] py-0.5 text-[10.5px]">
          <dt className="text-[#8b949e]">{k}</dt>
          <dd className="break-all text-right font-mono text-[#e6eaee]">{v == null ? "—" : String(v)}</dd>
        </div>
      ))}
      <details className="mt-1 text-[10.5px]">
        <summary className="cursor-pointer text-[#8b949e]">all fields (engine JSON)</summary>
        <pre className="mt-1 max-h-64 overflow-auto rounded bg-[#0e1116] p-2 font-mono text-[9.5px] text-[#8b949e]">
          {JSON.stringify(b, null, 1)}
        </pre>
      </details>
    </dl>
  );
}
