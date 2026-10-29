"use client";

/** PinSelected panel (§7.4 item 2): the tile card (engine-resolved), cached results for the tile,
 *  and the Scan control — enabled only when every precondition holds, disabled with the EXACT
 *  reason otherwise. Running state: verbatim progress stages, step_index/step_total, elapsed
 *  timer, aria-live, leave-and-return note. 409 → "engine busy" + Follow run. */
import { useEffect, useState } from "react";
import Link from "next/link";
import { timeAgo } from "@/lib/format";
import type { CachedSummary, Health, RunState, TileInfo } from "@/lib/types";

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

function Progress(props: { runState: RunState; onFollow: () => void }) {
  const [elapsed, setElapsed] = useState(0);
  const run = props.runState;
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [run.id]);
  const stage = String((run.progress as { stage?: string }).stage ?? "queued");
  const stepIndex = (run.progress as { step_index?: number }).step_index;
  const stepTotal = (run.progress as { step_total?: number }).step_total;
  return (
    <div className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-3" role="status" aria-live="polite">
      <p className="text-sm font-semibold text-[#7a6438]">Scanning… {stage}</p>
      {stepIndex != null && stepTotal != null && (
        <p className="mono mt-0.5 text-[11px] text-[#7a6438]">
          step {stepIndex}/{stepTotal}
        </p>
      )}
      <p className="mono mt-0.5 text-[11px] text-[#7a6438]">elapsed {elapsed}s</p>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e7e2da]">
        <div
          className="h-full bg-[#b08d57] transition-all"
          style={{ width: stepIndex && stepTotal ? `${Math.round((stepIndex / stepTotal) * 100)}%` : "12%" }}
        />
      </div>
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
