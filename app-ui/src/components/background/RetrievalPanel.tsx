"use client";

/** RetrievalPanel — Resolve → Plan → Download → Inspect → Preprocess as visible, cancellable,
 *  resumable stages (UI-build-3). Every number here is read from the engine's retrieval manifest
 *  (GET /api/retrievals/{id}); nothing is estimated except the one labelled "about N min left",
 *  which is planned bytes ÷ the MB/s measured over the last polls. Transport only; no science. */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { engineJson, EngineError } from "@/lib/engineFetch";
import { bgFetch } from "@/lib/bgApi";

interface PlanRow { granule_id: string; product: string; grid?: string; layer?: string; chunks?: number; bytes?: number | null; error?: string; note?: string }
interface GRow {
  granule_id: string; product: string; start?: string | null; state: string;
  bytes_planned: number | null; bytes_done: number; chunks_planned: number | null; chunks_done: number;
  seconds: number | null; error: string | null; source?: string; note?: string;
}
interface Flag { code: string; severity: string; message: string }
interface Manifest {
  id: string; status: string; stage: string; mode: string; error: string | null; tile: string | null;
  granules: GRow[]; plan: PlanRow[]; flags: Flag[]; storage_note?: string;
  totals?: { bytes_planned?: number; bytes_done?: number; stored?: number; granules?: number; failed?: number };
  net?: { bytes_fetched?: number; bytes_useful?: number; requests?: number; retries?: number };
  caps?: { max_gcov_epochs?: number; max_gunw_pairs?: number }; seconds?: number;
}
interface ClipRow { granule_id: string; product: string; grid: string; layer: string; shape: number[]; dtype: string; bytes: number }
interface Stats { valid: number; total: number; min?: number; max?: number; mean?: number; std?: number; hist?: { counts: number[]; edges: number[] }; note?: string }

const STAGES = ["Find granules", "Plan download", "Download", "Inspect", "Preprocess", "Pipelines"] as const;
const mb = (b?: number | null) => (b == null ? "—" : `${(b / 1e6).toFixed(b < 1e7 ? 2 : 1)} MB`);
const RUNNING = ["queued", "running", "cancelling"];

function stageIndex(m: Manifest | null, hasFound: boolean, preprocessRun: string | null): number {
  if (preprocessRun) return 4;
  if (!m) return hasFound ? 1 : 0;
  if (m.status === "running" || m.status === "queued" || m.status === "cancelling") return m.stage === "download" ? 2 : 1;
  if (m.status === "planned") return 1;
  if (m.totals?.stored) return 3;
  return 2;
}

/** Plain-language statement of what is slow and why — only from measured values. */
function slowLine(m: Manifest, mbps: number | null): string {
  if (m.status === "cancelling") return "Cancelling — in-flight requests finish, nothing new is asked; stored clips stay usable.";
  if (m.stage === "resolve") return "Asking NASA/ASF which granules cover the tile.";
  if (m.stage === "plan") return "Reading each granule's layout index (small metadata reads) to count the exact bytes this window needs.";
  const planned = m.totals?.bytes_planned ?? 0;
  const left = Math.max(0, planned - (m.totals?.bytes_done ?? 0));
  if (m.stage === "download" && mbps && mbps > 0.05)
    return `Waiting on NASA: ${mbps.toFixed(1)} MB/s; ${mb(planned)} planned, about ${Math.max(1, Math.round(left / 1e6 / mbps / 60))} min left.`;
  if (m.stage === "download") return `Downloading — ${mb(planned)} planned. Speed not measurable yet.`;
  return "";
}

export default function RetrievalPanel({ lon, lat, levels, foundCount }: { lon: number | null; lat: number | null; levels: string[]; foundCount: number }) {
  const [m, setM] = useState<Manifest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lookback, setLookback] = useState("60");
  const [maxGcov, setMaxGcov] = useState("");
  const [maxGunw, setMaxGunw] = useState("");
  const [mbps, setMbps] = useState<number | null>(null);
  const [clips, setClips] = useState<ClipRow[] | null>(null);
  const [sel, setSel] = useState<ClipRow | null>(null);
  const [img, setImg] = useState<{ url: string; label: string } | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [clipErr, setClipErr] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const last = useRef<{ t: number; b: number } | null>(null);
  const idRef = useRef<string | null>(null);

  const say = (e: unknown) => setErr(e instanceof EngineError ? e.full : e instanceof Error ? e.message : String(e));

  const start = useCallback(async (mode: "plan" | "retrieve") => {
    if (lon == null || lat == null) { setErr("Search a box first — the retrieval needs the tile's location."); return; }
    setErr(null); setBusy(true); setClips(null); setSel(null); setImg(null); setStats(null); setRunId(null); last.current = null;
    try {
      const body: Record<string, unknown> = { lon, lat, mode, lookback_days: Number(lookback) || 60, products: levels.filter((l) => l === "GUNW" || l === "GCOV") };
      if (maxGcov) body.max_gcov_epochs = Number(maxGcov);
      if (maxGunw) body.max_gunw_pairs = Number(maxGunw);
      const r = await engineJson<{ id: string }>("/api/retrievals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      idRef.current = r.id;
      setM(await engineJson<Manifest>(`/api/retrievals/${r.id}`));
    } catch (e) { say(e); } finally { setBusy(false); }
  }, [lon, lat, levels, lookback, maxGcov, maxGunw]);

  // poll while running
  useEffect(() => {
    if (!m || !RUNNING.includes(m.status)) return;
    const t = setInterval(async () => {
      try {
        const j = await engineJson<Manifest>(`/api/retrievals/${m.id}`);
        const b = j.net?.bytes_fetched;
        if (b != null) {
          const now = Date.now();
          if (last.current && now > last.current.t) setMbps(((b - last.current.b) / 1e6) / ((now - last.current.t) / 1000));
          last.current = { t: now, b };
        }
        setM(j); setErr(null);
      } catch (e) { say(e); }
    }, 1500);
    return () => clearInterval(t);
  }, [m]);

  const cancel = async () => { if (m) try { await engineJson(`/api/retrievals/${m.id}/cancel`, { method: "POST" }); } catch (e) { say(e); } };

  // stored clips (token-gated)
  const loadClips = useCallback(async () => {
    if (!m) return;
    setClipErr(null);
    try {
      const res = await bgFetch(`/api/retrievals/${m.id}/clips`);
      const j = await res.json();
      if (!res.ok) throw new Error(j?.detail?.error ?? `${res.status}`);
      setClips(j.clips as ClipRow[]);
    } catch (e) { setClipErr(e instanceof Error ? e.message : String(e)); }
  }, [m]);
  useEffect(() => { if (m && !RUNNING.includes(m.status) && (m.totals?.stored ?? 0) > 0 && clips === null) void loadClips(); }, [m, clips, loadClips]);

  const open = async (c: ClipRow) => {
    if (!m) return;
    setSel(c); setImg(null); setStats(null); setClipErr(null);
    const q = new URLSearchParams({ granule: c.granule_id, grid: c.grid, layer: c.layer });
    try {
      const [ri, rs] = await Promise.all([
        bgFetch(`/api/retrievals/${m.id}/clips/image?${q}&max_px=420`),
        bgFetch(`/api/retrievals/${m.id}/clips/stats?${q}`),
      ]);
      if (ri.ok) {
        const meta = JSON.parse(ri.headers.get("X-Img-Meta") ?? "{}") as { label?: string };
        setImg({ url: URL.createObjectURL(await ri.blob()), label: meta.label ?? "" });
      } else setClipErr((await ri.json().catch(() => ({})))?.detail?.error ?? `image: ${ri.status}`);
      if (rs.ok) setStats(await rs.json());
    } catch (e) { setClipErr(e instanceof Error ? e.message : String(e)); }
  };

  const download = async (path: string, name: string) => {
    try {
      const res = await bgFetch(path);
      if (!res.ok) throw new Error(`${res.status}`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob()); a.download = name; a.click();
    } catch (e) { setClipErr(e instanceof Error ? e.message : String(e)); }
  };

  const preprocess = async () => {
    if (!m) return;
    setErr(null);
    try { const r = await engineJson<{ run_id: string }>(`/api/retrievals/${m.id}/preprocess`, { method: "POST" }); setRunId(r.run_id); }
    catch (e) { say(e); }
  };

  const active = stageIndex(m, foundCount > 0, runId);
  const running = !!m && RUNNING.includes(m.status);
  const done = m?.granules.filter((g) => ["stored", "skipped", "failed", "cancelled"].includes(g.state)).length ?? 0;
  const planByGranule = m?.plan ?? [];

  return (
    <section aria-label="retrieval stages" className="border-b border-[#2a313c] bg-[#10141a] px-4 py-3 text-xs text-[#c9d1d9]">
      <ol className="mb-3 flex flex-wrap gap-1" aria-label="stages">
        {STAGES.map((s, i) => (
          <li key={s} aria-current={i === active ? "step" : undefined}
              className={`rounded px-2 py-0.5 ${i === active ? "bg-[#1f6f7c] text-white" : i < active ? "bg-[#1b2a2e] text-[#7ec8d3]" : "bg-[#1a1f27] text-[#6b7480]"}`}>
            {i + 1} {s}
          </li>
        ))}
      </ol>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-0.5">lookback (days)
          <input value={lookback} onChange={(e) => setLookback(e.target.value)} className="w-20 rounded border border-[#2a313c] bg-[#0e1116] px-1 py-0.5" /></label>
        <label className="flex flex-col gap-0.5">max GCOV epochs
          <input value={maxGcov} placeholder="default" onChange={(e) => setMaxGcov(e.target.value)} className="w-24 rounded border border-[#2a313c] bg-[#0e1116] px-1 py-0.5" /></label>
        <label className="flex flex-col gap-0.5">max GUNW pairs
          <input value={maxGunw} placeholder="default" onChange={(e) => setMaxGunw(e.target.value)} className="w-24 rounded border border-[#2a313c] bg-[#0e1116] px-1 py-0.5" /></label>
        <button type="button" disabled={busy || running} onClick={() => start("plan")} className="rounded border border-[#3fb6c9] px-2 py-1 disabled:opacity-50">Plan (no download)</button>
        <button type="button" disabled={busy || running} onClick={() => start("retrieve")} className="rounded bg-[#1f6f7c] px-2 py-1 text-white disabled:opacity-50">Plan + download</button>
        {running && <button type="button" onClick={cancel} className="rounded border border-[#c97a7a] px-2 py-1 text-[#e6a0a0]">Cancel</button>}
        {m && !running && m.status !== "planned" && (m.totals?.stored ?? 0) < (m.totals?.granules ?? 0) &&
          <button type="button" onClick={() => start("retrieve")} className="rounded border border-[#3fb6c9] px-2 py-1">Resume (re-run; stored granules are skipped)</button>}
      </div>
      <p className="mt-1 text-[#8b949e]">Caps decide how many granules are asked for. Nothing is downloaded until you press “Plan + download”.</p>

      {err && <p role="alert" className="mt-2 rounded border border-[#6b2f2f] bg-[#2a1515] px-2 py-1 text-[#f0a0a0]">{err}</p>}

      {m && (
        <div className="mt-3 space-y-3">
          <p role="status">
            <b>{m.status}</b>
            {m.tile ? ` · tile ${m.tile}` : ""}
            {m.stage === "download" || m.status === "done" || m.status === "cancelled" ? ` · granule ${done}/${m.granules.length} · ${mb(m.totals?.bytes_done)} of ${mb(m.totals?.bytes_planned)}` : ""}
            {m.net?.bytes_fetched != null ? ` · ${mb(m.net.bytes_fetched)} on wire (${mb(m.net.bytes_useful)} useful)` : ""}
          </p>
          {running && <p className="text-[#e6c977]">{slowLine(m, mbps)}</p>}
          {m.error && <p role="alert" className="text-[#f0a0a0]">What failed: {m.error}. What to do: fix the cause named above and press Resume — stored granules are kept.</p>}
          {m.flags.filter((f) => f.severity !== "info").map((f, i) => (
            <p key={i} className="text-[#f0b36a]">⚠ {f.message}{/search_incomplete/.test(f.code) ? " — re-run the search from the box above (Retry)." : ""}</p>
          ))}

          {planByGranule.length > 0 && (
            <details open={m.status === "planned"}>
              <summary className="cursor-pointer">Plan — {mb(m.totals?.bytes_planned)} compressed in {planByGranule.reduce((a, r) => a + (r.chunks ?? 0), 0)} chunks (exact, from each granule&apos;s chunk table)</summary>
              <div className="max-h-56 overflow-auto"><table className="mt-1 w-full text-left">
                <thead className="text-[#8b949e]"><tr><th>granule</th><th>layer</th><th>chunks</th><th>MB</th></tr></thead>
                <tbody>{planByGranule.map((r, i) => (
                  <tr key={i}><td title={r.granule_id}>{r.product} …{r.granule_id.slice(-24)}</td><td>{r.layer?.split("__").slice(-2).join("/") ?? "—"}</td>
                    <td>{r.chunks ?? "—"}</td><td>{r.error ? <span className="text-[#f0a0a0]">{r.error}</span> : r.note ?? mb(r.bytes)}</td></tr>))}</tbody>
              </table></div>
            </details>
          )}

          <details open>
            <summary className="cursor-pointer">Per granule ({m.granules.length})</summary>
            <div className="max-h-56 overflow-auto"><table className="mt-1 w-full text-left">
              <thead className="text-[#8b949e]"><tr><th>granule</th><th>state</th><th>MB done / planned</th><th>s</th><th>note</th></tr></thead>
              <tbody>{m.granules.map((g) => (
                <tr key={g.granule_id}><td title={g.granule_id}>{g.product} …{g.granule_id.slice(-24)}</td>
                  <td className={g.state === "failed" ? "text-[#f0a0a0]" : g.state === "stored" ? "text-[#7ec8d3]" : ""}>{g.state}{g.source ? ` (${g.source})` : ""}</td>
                  <td>{mb(g.bytes_done)} / {mb(g.bytes_planned)}</td><td>{g.seconds ?? "—"}</td><td>{g.error ?? g.note ?? ""}</td></tr>))}</tbody>
            </table></div>
          </details>

          {m.storage_note && <p className="text-[#8b949e]">{m.storage_note}.</p>}
          {!running && (m.totals?.stored ?? 0) > 0 && (
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => download(`/api/retrievals/${m.id}/download.zip`, `${m.id}.zip`)} className="rounded border border-[#3fb6c9] px-2 py-1">Download retrieval (zip)</button>
              <button type="button" onClick={preprocess} className="rounded bg-[#1f6f7c] px-2 py-1 text-white">Preprocess from this retrieval (no network)</button>
              {runId && <Link className="underline" href="/background/preprocessing">run {runId} started from the store — pick it in Preprocessing →</Link>}
            </div>
          )}

          {clips && (
            <div>
              <p className="mb-1">Stored clips — read from the engine&apos;s store, not re-downloaded</p>
              <div className="flex flex-wrap gap-3">
                <div className="max-h-64 min-w-[20rem] flex-1 overflow-auto"><table className="w-full text-left">
                  <thead className="text-[#8b949e]"><tr><th>granule</th><th>layer</th><th>shape</th><th>dtype</th><th>MB</th></tr></thead>
                  <tbody>{clips.map((c, i) => (
                    <tr key={i} onClick={() => open(c)} className={`cursor-pointer hover:bg-[#1a2430] ${sel === c ? "bg-[#1a2430]" : ""}`}>
                      <td>{c.product} …{c.granule_id.slice(-18)}</td><td>{c.layer.split("__").slice(-2).join("/")}</td><td>{c.shape.join("×")}</td><td>{c.dtype}</td><td>{mb(c.bytes)}</td></tr>))}</tbody>
                </table></div>
                {sel && (
                  <div className="w-[26rem] max-w-full">
                    {img ? <><img src={img.url} alt={`stored ${sel.layer}`} className="max-h-72 border border-[#2a313c]" style={{ imageRendering: "pixelated" }} /><p className="text-[#8b949e]">{img.label}</p></> : <p>loading image…</p>}
                    {stats?.hist && (
                      <div className="mt-1"><div className="flex h-12 items-end gap-px" aria-label="histogram">
                        {stats.hist.counts.map((c, i) => <div key={i} className="flex-1 bg-[#3fb6c9]" style={{ height: `${(100 * c) / Math.max(...stats.hist!.counts, 1)}%` }} />)}</div>
                        <p className="text-[#8b949e]">valid {stats.valid}/{stats.total} · min {stats.min?.toPrecision(4)} · max {stats.max?.toPrecision(4)} · mean {stats.mean?.toPrecision(4)} · σ {stats.std?.toPrecision(4)}</p></div>)}
                    <button type="button" className="mt-1 underline" onClick={() => download(`/api/retrievals/${m.id}/export.npz?granule=${encodeURIComponent(sel.granule_id)}`, `${sel.granule_id}.npz`)}>export this granule (.npz)</button>
                  </div>
                )}
              </div>
            </div>
          )}
          {clipErr && <p role="alert" className="text-[#f0a0a0]">{clipErr}</p>}
        </div>
      )}
    </section>
  );
}
