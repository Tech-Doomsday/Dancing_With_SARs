"use client";

/** PinSelected panel (§7.4 item 2): the tile card (engine-resolved), cached results for the tile,
 *  and the Scan control — enabled only when every precondition holds, disabled with the EXACT
 *  reason otherwise. Running state: verbatim progress stages, step_index/step_total, elapsed
 *  timer, aria-live, leave-and-return note. 409 → "engine busy" + Follow run. */
import { useEffect, useState } from "react";
import Link from "next/link";
import { timeAgo } from "@/lib/format";
import { granulesBbox } from "@/lib/api";
import type { CachedSummary, Health, RunState, TileInfo } from "@/lib/types";
import type { GranuleExplorerResponse } from "@/lib/types";

export interface ScanPanelProps {
  tile: TileInfo | null;
  tileError: string | null;
  tileLoading: boolean;
  pin: [number, number] | null;
  cachedForTile: CachedSummary[] | null;
  health: Health | null;
  engineOnline: boolean;
  implementedCount: number | null;
  totalCount: number | null;
  runState: RunState | null;
  runError: string | null;
  busyRunId: string | null;
  onScan: () => void;
  onOpenResult: (bundleId: string) => void;
  onOpenDataTab: () => void;
}

function ScanButton(props: ScanPanelProps) {
  const h = props.health;
  const tileReady = props.tile != null && !props.tileError;
  const scansOn = h?.scans_enabled === true;
  const creds = h?.earthdata_credentials === "present";
  const enabled = tileReady && props.engineOnline && scansOn && creds && props.runState == null;
  const reason = !props.engineOnline
    ? "the engine is offline"
    : !tileReady
      ? props.tileError ?? "no tile resolved for this point"
      : !scansOn
        ? "scans are disabled on this engine (set NISAR_ENABLE_SCANS=1)"
        : !creds
          ? "no Earthdata credentials in the environment (EARTHDATA_TOKEN or EARTHDATA_USERNAME + EARTHDATA_PASSWORD)"
          : props.runState
            ? "this scan is already running"
            : null;
  const hasCached = (props.cachedForTile?.length ?? 0) > 0;
  return (
    <div>
      <button
        onClick={props.onScan}
        disabled={!enabled}
        className={`w-full rounded-lg px-3 py-2 text-sm font-semibold ${
          enabled
            ? "bg-[#2c5f58] text-white hover:bg-[#245049]"
            : "cursor-not-allowed border border-[#e7e2da] bg-[#f0ece4] text-[#8a837b]"
        }`}
      >
        {hasCached ? "Scan for new data" : "Scan this tile"}
      </button>
      {reason && (
        <p role="status" className="mt-1 text-[11px] text-[#7a6438]">
          Scan is off: {reason}
        </p>
      )}
      <p className="mt-1 text-[10px] leading-relaxed text-[#8a837b]">
        fetches the newest NISAR data for this 10 km tile and runs every pipeline; identical data
        overwrites the cached verdict in place, new data adds a new scan, earlier scans stay
        available.
        {props.implementedCount != null && props.totalCount != null && (
          <> {props.totalCount - props.implementedCount} of {props.totalCount} pipelines are not modelled yet
            — those honestly report so.</>
        )}
      </p>
    </div>
  );
}

type ScanNet = {
  mb: number; mb_useful: number; requests: number; inflight: number | null; retries: number; resolves: number;
  mbps_now: number; mbps_avg: number; idle_s: number | null;
};
type ScanGranules = {
  product: string; total: number; done: number; failed: number; skipped: number; cached: number; in_flight: number;
  elapsed_s: number; eta_s: number | null; cum_done: number; cum_total: number;
  oldest: { id: string; age_s: number }[]; recent: { id: string; how: string; s: number }[];
};
type ScanInfo = {
  p01: { lines: string[]; dropped_older: number; degraded: boolean; incomplete: boolean; errors: Record<string, string> } | null;
  flag_counts: Record<string, number>;
  recent_flags: { severity: string; step: string; code: string; message: string }[];
};
type ScanProgress = {
  stage?: string; step_index?: number; step_total?: number; elapsed_s?: number; net?: ScanNet; granules?: ScanGranules;
  cache?: Record<string, number>; search?: { truncated: boolean; incomplete: boolean };
  steps?: { code: string; name: string; status: string; seconds: number }[]; info?: ScanInfo;
};

const fmtDur = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);
const shortId = (id: string) => (id.length > 46 ? `${id.slice(0, 22)}…${id.slice(-20)}` : id);

function Progress(props: { runState: RunState; onFollow: () => void }) {
  const [elapsed, setElapsed] = useState(0);
  const run = props.runState;
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [run.id]);
  const pr = run.progress as ScanProgress;
  const stage = String(pr.stage ?? "queued");
  const stepIndex = pr.step_index;
  const stepTotal = pr.step_total;
  const shownElapsed = typeof pr.elapsed_s === "number" ? Math.round(pr.elapsed_s) : elapsed;
  const net = pr.net;
  const g = pr.granules && pr.granules.total > 0 ? pr.granules : undefined;
  const stalled = net?.idle_s != null && net.idle_s > 30;
  const info = pr.info;
  const flagTxt = info ? Object.entries(info.flag_counts).map(([k, v]) => `${v} ${k}`).join(" · ") : "";
  // bar: finished steps + the running step's share when its granule counter is known
  const stepFrac = g ? g.done / g.total : 0;
  const barPct = stepIndex && stepTotal ? Math.max(4, Math.round(((stepIndex - 1 + stepFrac) / stepTotal) * 100)) : 4;
  return (
    <div className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-3" role="status" aria-live="polite">
      <p className="text-sm font-semibold text-[#7a6438]">Scanning… {stage}</p>
      {stepIndex != null && stepTotal != null && (
        <p className="mono mt-0.5 text-[11px] text-[#7a6438]">
          step {stepIndex}/{stepTotal} · running now
        </p>
      )}
      <p className="mono mt-0.5 text-[11px] text-[#7a6438]">elapsed {fmtDur(shownElapsed)}</p>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e7e2da]">
        <div className="h-full bg-[#b08d57] transition-all" style={{ width: `${barPct}%` }} />
      </div>

      {g && (
        <p className="mono mt-2 text-[11px] text-[#7a6438]" data-testid="scan-granules">
          {g.product || "granules"} {g.done}/{g.total} done
          {g.in_flight > 0 ? ` · ${g.in_flight} in flight` : ""}
          {g.cached > 0 ? ` · ${g.cached} from cache` : ""}
          {g.skipped > 0 ? ` · ${g.skipped} skipped` : ""}
          {g.failed > 0 ? ` · ${g.failed} FAILED` : ""}
          {g.eta_s != null ? ` · rough ETA ${fmtDur(g.eta_s)}` : ""}
        </p>
      )}
      {net && (
        <div className="mono mt-1 space-y-0.5 text-[11px] text-[#7a6438]" data-testid="scan-net">
          <p>
            {net.mb.toFixed(1)} MB on wire ({net.mb_useful.toFixed(1)} MB useful) · {net.mbps_now.toFixed(2)} MB/s now ·{" "}
            {net.mbps_avg.toFixed(2)} avg
          </p>
          <p>
            {net.requests} requests{net.inflight != null ? ` · ${net.inflight} in flight` : ""} · {net.retries} retries ·{" "}
            {net.resolves} URL resolves
          </p>
          <p className={stalled ? "font-semibold text-[#9e3b2c]" : ""}>
            {net.idle_s == null ? "no bytes yet (resolving URLs / opening files)" : `last byte ${fmtDur(net.idle_s)} ago${stalled ? " — stalled? reads time out and retry" : ""}`}
          </p>
        </div>
      )}
      {g && g.oldest.length > 0 && g.oldest[0].age_s > 20 && (
        <p className="mono mt-1 text-[10px] text-[#7a6438]" title={g.oldest.map((o) => o.id).join("\n")}>
          slowest in flight: {shortId(g.oldest[0].id)} ({fmtDur(g.oldest[0].age_s)})
        </p>
      )}

      <details className="mt-2" open>
        <summary className="cursor-pointer text-[11px] font-semibold text-[#7a6438]">details</summary>
        <div className="mono mt-1 space-y-1 text-[10px] leading-snug text-[#7a6438]">
          {pr.steps && pr.steps.length > 0 && (
            <p>
              done:{" "}
              {pr.steps.map((s) => `${s.code} ${s.status === "ok" ? "✓" : s.status === "skipped" ? "–" : "✗"} ${fmtDur(s.seconds)}`).join(" · ")}
            </p>
          )}
          {info?.p01 && info.p01.lines.map((l, k) => <p key={k}>P01: {l}</p>)}
          {pr.search?.incomplete && (
            <p className="font-semibold text-[#9e3b2c]">ASF/CMR returned a short listing even after a retry — some granules may be missing</p>
          )}
          {pr.cache && Object.keys(pr.cache).length > 0 && (
            <p>
              cache:{" "}
              {Object.entries(pr.cache).filter(([, v]) => v > 0).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ") || "no hits yet"}
            </p>
          )}
          {g && g.recent.length > 0 && (
            <p title={g.recent.map((r) => `${r.id}: ${r.how} ${r.s}s`).join("\n")}>
              last finished: {g.recent.map((r) => `${r.how} ${fmtDur(r.s)}`).join(" · ")}
            </p>
          )}
          {flagTxt && <p>flags so far: {flagTxt}</p>}
          {info?.recent_flags.map((f, k) => (
            <p key={k} className={f.severity === "error" ? "text-[#9e3b2c]" : ""}>
              {f.severity} {f.step} {f.code}: {f.message}
            </p>
          ))}
        </div>
      </details>

      <p className="mt-2 text-[10px] text-[#7a6438]">
        this cannot be cancelled yet — you can leave and return to this scan with its link.
      </p>
      <Link
        href={`/?panel=results&scan=${run.id}`}
        className="mono mt-1 inline-block text-[11px] text-[#2c5f58] underline"
        onClick={props.onFollow}
      >
        {run.id}
      </Link>
    </div>
  );
}

/** Check coverage (§7.4) — never automatic: the button fetches GET /api/granules for the tile's
 *  bbox and shows counts per stack, the last acquisition, the largest gap and any
 *  truncation/degradation notices — all from the engine, nothing invented. "Open in Granule
 *  Explorer" hands the same box to /granules. */
function CheckCoverage(props: { tile: TileInfo; engineOnline: boolean; credentials: "present" | "absent" | undefined }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<GranuleExplorerResponse | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  if (!props.engineOnline || props.credentials !== "present") {
    return (
      <p className="mt-1.5 text-[11px] text-[#8a837b]">
        coverage check needs the engine online with Earthdata credentials — the Granule Explorer
        shows the same answer for any box.
      </p>
    );
  }
  const [w, s, e, n] = props.tile.bbox_lonlat;
  const check = () => {
    setState("loading");
    setMessage(null);
    granulesBbox({ west: w, south: s, east: e, north: n, level: "GUNW,GCOV" })
      .then((r) => {
        setResult(r);
        setState("done");
      })
      .catch((err) => {
        setMessage(err instanceof Error ? err.message : String(err));
        setState("error");
      });
  };
  return (
    <div className="mt-2 border-t border-[#f0ece4] pt-2">
      <button
        onClick={check}
        disabled={state === "loading"}
        className="rounded-md border border-[#2c5f58] px-2 py-1 text-[11px] text-[#2c5f58] hover:bg-[#eff5f3] disabled:opacity-50"
      >
        {state === "loading" ? "checking…" : "Check coverage (GUNW + GCOV)"}
      </button>
      <a
        href={`/background/retrieval?west=${w}&south=${s}&east=${e}&north=${n}&level=GUNW,GCOV`}
        className="ml-2 text-[11px] text-[#2c5f58] underline"
      >
        Open in Granule Explorer
      </a>
      {message && (
        <p role="alert" className="mono mt-1 text-[11px] text-[#9e3b2c]">{message}</p>
      )}
      {result && (
        <div className="mt-1.5 text-[11px] text-[#4a443c]" role="status">
          <p>
            {result.n_total} granules in this tile&apos;s box
            {result.truncated ? " — budget hit, the list is incomplete" : ""}
            {result.degraded ? " — served from stale cache" : ""}.
          </p>
          <ul className="mt-1 space-y-0.5">
            {result.stacks.map((st) => (
              <li key={st.key} className="mono">
                {st.key} ({st.direction === "D" ? "descending" : st.direction === "A" ? "ascending" : "?"}) · {st.n_granules} granules
                {st.last ? ` · last ${timeAgo(st.last)}` : ""}
                {st.max_gap_days != null ? ` · largest gap ${st.max_gap_days} d` : ""}
              </li>
            ))}
            {result.stacks.length === 0 && <li className="text-[#8a837b]">no stacks — the engine found no granules in the window</li>}
          </ul>
          <p className="mt-1 text-[10px] text-[#8a837b]">
            a coverage count never implies a detection — the scan decides that.
          </p>
        </div>
      )}
    </div>
  );
}

export default function ScanPanel(props: ScanPanelProps) {
  const [showDataUsed, setShowDataUsed] = useState(false);
  if (props.tileError) {
    return (
      <div className="p-3 text-xs">
        <p role="alert" className="mono rounded-lg border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-[#9e3b2c]">
          {props.tileError}
        </p>
        <p className="mt-2 text-[11px] text-[#8a837b]">the pin stays where you put it; scanning is disabled here.</p>
      </div>
    );
  }
  if (props.tileLoading) {
    return <div className="p-3"><div className="skeleton h-24 rounded-lg" aria-busy="true" /></div>;
  }
  if (!props.pin) {
    return (
      <div className="p-3 text-xs text-[#8a837b]">
        click the globe or type coordinates to pick a place — the tile it falls in, its cached
        results and the Scan control appear here.
      </div>
    );
  }
  if (!props.tile) {
    // pin placed, but the engine's tile-at answer has not landed yet (or did not resolve)
    return (
      <div className="p-3 text-xs text-[#8a837b]" role="status">
        resolving the MGRS tile for this point…
      </div>
    );
  }
  const [lon, lat] = props.tile.center_lonlat;
  const cachedRows = props.cachedForTile ?? [];
  const run = props.runState;
  const done = run?.status === "ok";
  const eq = done ? run.results.find((r) => r.pipeline === "earthquake_coseismic") : null;
  const insufficient = eq?.status === "INSUFFICIENT_EVIDENCE";

  return (
    <div className="space-y-3 p-3">
      <div className="rounded-lg border border-[#e7e2da] bg-white p-3">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-semibold">{props.tile.id}</h3>
          <span className="mono text-[10px] text-[#8a837b]">EPSG:{props.tile.epsg} · {props.tile.size_m / 1000} km</span>
        </div>
        <p className="mono mt-0.5 text-[11px] text-[#4a443c]">
          pin ({props.pin[0].toFixed(4)}, {props.pin[1].toFixed(4)}) · tile centre ({lon.toFixed(4)}, {lat.toFixed(4)})
        </p>
        <button
          onClick={props.onOpenDataTab}
          className="mt-1.5 rounded-md border border-[#2c5f58] px-2 py-1 text-[11px] text-[#2c5f58] hover:bg-[#eff5f3]"
        >
          Data tab: how much SAR data exists here
        </button>
        <CheckCoverage tile={props.tile} engineOnline={props.engineOnline} credentials={props.health?.earthdata_credentials} />
      </div>

      {props.runError && (
        <p role="alert" className="mono rounded-lg border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-[11px] text-[#9e3b2c]">
          {props.runError}
          {props.busyRunId && (
            <button onClick={() => props.onOpenResult(props.busyRunId as string)} className="ml-2 text-[#2c5f58] underline">
              follow run {props.busyRunId}
            </button>
          )}
        </p>
      )}

      {run && run.status !== "ok" && run.status !== "error" && <Progress runState={run} onFollow={() => undefined} />}
      {run?.status === "error" && (
        <p role="alert" className="mono rounded-lg border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-[11px] text-[#9e3b2c]">
          {run.error}
        </p>
      )}

      {done && insufficient && eq && (
        <div className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-3 text-xs">
          <p className="font-semibold text-[#7a6438]">{eq.status.replace(/_/g, " ").toLowerCase()}</p>
          <p className="mt-1 text-[#4a443c]">
            {eq.summary}
          </p>
          <button
            onClick={() => setShowDataUsed((v) => !v)}
            className="mt-1.5 text-[11px] text-[#2c5f58] underline"
          >
            {showDataUsed ? "hide" : "show"} data used
          </button>
          {showDataUsed && (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-[#4a443c]">
              {eq.data_used.map((d, i) => (
                <li key={i} className="mono">{d}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {cachedRows.length > 0 && (
        <div className="rounded-lg border border-[#e7e2da] bg-white p-3">
          <h4 className="text-xs font-semibold">Cached results for this tile</h4>
          <ul className="mt-1.5 space-y-1.5">
            {cachedRows.map((r) => (
              <li key={r.bundle_id + r.pipeline} className="flex items-center justify-between gap-2 text-[11px]">
                <span className="text-[#4a443c]">
                  <span className="mono">{r.pipeline}</span> · {r.status.replace(/_/g, " ").toLowerCase()} · {timeAgo(r.computed_at)}
                </span>
                <button onClick={() => props.onOpenResult(r.bundle_id)} className="text-[#2c5f58] hover:underline">
                  open
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ScanButton {...props} />
    </div>
  );
}
