"use client";

/** Stage 3 in the Data retrieval tab: start / watch / cancel a download of the clips for this box.
 *  Every number shown is one the engine reported; nothing is estimated. Where the engine has not
 *  measured something (bytes), the panel says "not measured". */
import { useEffect, useRef, useState } from "react";
import { retrievals, type Retrieval, type RetrievalGranule, pollWhile } from "@/lib/api";

const STATE_TEXT: Record<RetrievalGranule["state"], string> = {
  queued: "waiting", fetching: "downloading", stored: "stored", skipped: "skipped", failed: "failed", cancelled: "cancelled",
};
const STATE_COLOR: Record<RetrievalGranule["state"], string> = {
  queued: "text-[#8b949e]", fetching: "text-[#7fd8e6]", stored: "text-[#7ee2a0]",
  skipped: "text-[#d9b36a]", failed: "text-[#ff8b8b]", cancelled: "text-[#d9b36a]",
};
const STAGES = ["Find granules", "Plan download", "Download", "Inspect", "Preprocess", "Pipelines"];

function activeStage(r: Retrieval | null): number {
  if (!r) return 0;
  if (r.status === "queued" || r.stage === "finding granules") return 0;
  if (r.status === "running") return 2;
  return 3;                              // finished: the data is stored and ready to inspect
}

export default function RetrievalPanel({ bbox, levels }: { bbox: [number, number, number, number] | null; levels: string[] }) {
  const [lookback, setLookback] = useState("60");
  const [maxG, setMaxG] = useState("12");
  const [r, setR] = useState<Retrieval | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const stop = useRef<(() => void) | null>(null);

  useEffect(() => () => stop.current?.(), []);

  const follow = (id: string) => {
    stop.current?.();
    stop.current = pollWhile<Retrieval>(
      async () => {
        const v = await retrievals.get(id);
        return { done: v.status === "done" || v.status === "failed" || v.status === "cancelled", value: v };
      },
      setR, 1500, (m) => setErr(m || null),
    );
  };

  const start = async () => {
    if (!bbox) return;
    setErr(null); setStarting(true);
    try {
      const out = await retrievals.start({
        bbox, products: levels.filter((l) => l === "GUNW" || l === "GCOV"),
        lookback_days: Number(lookback), max_granules: maxG ? Number(maxG) : undefined,
      });
      follow(out.retrieval_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setStarting(false); }
  };

  const cancel = async () => {
    if (!r) return;
    try { await retrievals.cancel(r.id); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  const running = r?.status === "running" || r?.status === "queued";
  const products = levels.filter((l) => l === "GUNW" || l === "GCOV");
  const stage = activeStage(r);
  const finished = r ? r.counts.stored + r.counts.failed + r.counts.skipped + r.counts.cancelled : 0;

  return (
    <section aria-label="Retrieval" className="border-t border-[#262c36] px-4 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium text-[#d9dee4]">Download clips for this box</h2>
        <label className="flex items-center gap-1">Lookback (days)
          <input value={lookback} onChange={(e) => setLookback(e.target.value)} inputMode="numeric"
                 className="w-14 rounded border border-[#333b47] bg-transparent px-1.5 py-1" disabled={running} />
        </label>
        <label className="flex items-center gap-1">Newest per product
          <input value={maxG} onChange={(e) => setMaxG(e.target.value)} inputMode="numeric"
                 className="w-14 rounded border border-[#333b47] bg-transparent px-1.5 py-1" disabled={running} />
        </label>
        {!running ? (
          <button type="button" onClick={start} disabled={!bbox || products.length === 0 || starting}
                  className="rounded-md bg-[#2c5f58] px-3 py-1.5 font-medium text-white hover:bg-[#37736a] disabled:opacity-50">
            {r ? "Download again (stored granules are reused)" : "Download"}
          </button>
        ) : (
          <button type="button" onClick={cancel}
                  className="rounded-md border border-[#7a3b3b] px-3 py-1.5 text-[#ff8b8b] hover:bg-[#2a1717]">Cancel</button>
        )}
      </div>
      <p className="mt-1 text-[#8b949e]">
        Only the bytes for this box are downloaded, not whole granules. Stored on the engine&apos;s disk — on Colab that is
        lost when the session ends.{!bbox && " Enter a box first."}{bbox && products.length === 0 && " Select GUNW or GCOV."}
      </p>

      <ol className="mt-2 flex flex-wrap gap-x-3 gap-y-1" aria-label="Stages">
        {STAGES.map((s, i) => (
          <li key={s} className={i === stage && (running || r) ? "font-medium text-[#7fd8e6]" : i < stage ? "text-[#7ee2a0]" : "text-[#6b7480]"}>
            {i + 1} {s}{i === stage && running ? " (now)" : ""}
          </li>
        ))}
      </ol>

      {err && <p role="alert" className="mt-2 rounded border border-[#7a3b3b] bg-[#2a1717] px-2 py-1 text-[#ffb4b4]">{err}</p>}

      {r && (
        <div className="mt-2">
          <p>
            {r.status === "running" && <>Downloading: <b>{finished}/{r.total}</b> granules finished · {r.counts.fetching} downloading now. </>}
            {r.status === "done" && <>Done: <b>{r.counts.stored}</b> stored, {r.counts.failed} failed. </>}
            {r.status === "cancelled" && <>Cancelled: <b>{r.counts.stored}</b> stored and kept, {r.counts.cancelled} not started. </>}
            {r.status === "failed" && <>The retrieval failed: {r.error}. </>}
            {r.status === "queued" && <>Waiting to start. </>}
            <span className="text-[#6b7480]">Bytes per granule: not measured yet.</span>
          </p>
          {r.flags.map((f) => <p key={f} className="mt-1 text-[#d9b36a]">⚠ {f}</p>)}
          <div className="mt-2 max-h-56 overflow-auto rounded border border-[#262c36]">
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-[#12161d] text-[#8b949e]">
                <tr><th className="px-2 py-1">Granule</th><th className="px-2 py-1">State</th><th className="px-2 py-1">Seconds</th><th className="px-2 py-1">Note</th></tr>
              </thead>
              <tbody>
                {r.granules.map((g) => (
                  <tr key={g.granule_id} className="border-t border-[#1b212a]">
                    <td className="px-2 py-1 font-mono" title={g.granule_id}>{g.product} · {g.start?.slice(0, 10) ?? "?"} · {g.track ?? "?"}{g.direction ? g.direction[0] : ""}</td>
                    <td className={`px-2 py-1 ${STATE_COLOR[g.state]}`}>{STATE_TEXT[g.state]}</td>
                    <td className="px-2 py-1">{g.seconds ?? ""}</td>
                    <td className="px-2 py-1 text-[#8b949e]">{g.error ?? g.source ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
