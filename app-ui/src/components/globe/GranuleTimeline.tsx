"use client";

/** GranuleTimeline (§7.5) — a TRUE continuous UTC time scale (lib/timescale.ts, DEC11: no chart
 *  library), one row per stack (direction + track, the engine's own grouping), one bar per GUNW
 *  PairGroup (ref -> sec) and one marker per GCOV EpochGroup. Gaps carry the EXACT day count
 *  (never a judgement word); the gap threshold derives from the row's own median spacing.
 *  Pan (drag) / zoom (wheel, buttons) / keyboard operable; "today" guideline; ticks adapt. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import type { GranuleStack, GranuleSearchResponse } from "@/lib/types";
import {
  clamp, gapThresholdDays, pan as panScale, pxPerMs, ticks as ticksFor,
  tToX, zoom as zoomScale, type TimeScale,
} from "@/lib/timescale";
import { DIRECTION_COLORS, directionLabel } from "@/lib/status";

export interface TimelineSelection {
  stackKey: string;
  kind: "gunw" | "gcov";
  index: number;
}

const DAY_MS = 86_400_000;
const ROW_H = 26;
const LABEL_W = 92;

export interface GranuleTimelineProps {
  data: GranuleSearchResponse;
  selection: TimelineSelection | null;
  onSelect: (sel: TimelineSelection | null) => void;
  onWindowChange: (fromIso: string, toIso: string) => void;
}

interface Row {
  key: string;
  stack: GranuleStack;
  bars: { start: number; end: number; kind: "gunw"; index: number }[];
  marks: { at: number; kind: "gcov"; index: number }[];
}

function stackRows(stacks: Record<string, GranuleStack>): Row[] {
  const rows: Row[] = [];
  for (const key of Object.keys(stacks).sort((a, b) => {
    const da = a[0] === "D" ? 1 : 0;
    const db = b[0] === "D" ? 1 : 0;
    return da - db || a.localeCompare(b);
  })) {
    const stack = stacks[key];
    const bars: Row["bars"] = [];
    const marks: Row["marks"] = [];
    stack.gunw.forEach((p, index) => {
      if (!p.ref_time || !p.sec_time) return; // unresolved: no dates to draw (honest gap)
      const start = Date.parse(p.ref_time);
      const end = Date.parse(p.sec_time);
      if (Number.isFinite(start) && Number.isFinite(end)) bars.push({ start: Math.min(start, end), end: Math.max(start, end), kind: "gunw", index });
    });
    stack.gcov.forEach((e, index) => {
      const at = e.time ? Date.parse(e.time) : NaN;
      if (Number.isFinite(at)) marks.push({ at, kind: "gcov", index });
    });
    rows.push({ key, stack, bars, marks });
  }
  return rows;
}

export default function GranuleTimeline(props: GranuleTimelineProps) {
  const bounds = useMemo(() => ({
    min: Date.parse(props.data.window.t0),
    max: Date.parse(props.data.window.t1),
  }), [props.data.window.t0, props.data.window.t1]);
  const [scale, setScale] = useState<TimeScale>({ t0: bounds.min, t1: bounds.max, widthPx: 640 });
  const [width, setWidth] = useState(640);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{ x: number; t0: number; t1: number } | null>(null);
  const lastWindow = useRef<string>("");

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 640;
      setWidth(Math.max(280, w));
      setScale((s) => ({ ...s, widthPx: Math.max(280, w) }));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // the visible range feeds back into the query (debounced by the parent's effect)
  useEffect(() => {
    const from = new Date(scale.t0).toISOString().replace(/\.\d{3}Z$/, "Z");
    const to = new Date(scale.t1).toISOString().replace(/\.\d{3}Z$/, "Z");
    const sig = `${from}|${to}`;
    if (sig !== lastWindow.current) {
      lastWindow.current = sig;
      props.onWindowChange(from, to);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scale.t0, scale.t1]);

  useEffect(() => {
    setScale((s) => clamp({ ...s }, bounds));
  }, [bounds]);

  const rows = useMemo(() => stackRows(props.data.stacks), [props.data.stacks]);
  const height = rows.length * ROW_H + 34;
  const today = Date.now();
  const rawTicks = useMemo(() => ticksFor({ ...scale, widthPx: width }), [scale, width]);
  // §8.3 critique fix: drop labels that would overlap (month boundary + week tick 3px apart
  // rendered "1 Jun 20263 Jul 2026"); a nearby major (month) label replaces a colliding minor.
  const tickList = useMemo(() => {
    const px = (ms: number) => tToX({ ...scale, widthPx: width }, ms);
    const wOf = (t: (typeof rawTicks)[number]) => t.label.length * 5.7 + 8; // ~mono 9px/char + x offset
    const out: typeof rawTicks = [];
    for (const t of rawTicks) {
      const x = px(t.ms);
      if (x < 0) continue; // label would start off-canvas
      const last = out[out.length - 1];
      if (last && x < px(last.ms) + wOf(last)) {
        if (t.major && !last.major && x - px(last.ms) < 18) out[out.length - 1] = t;
        continue;
      }
      out.push(t);
    }
    return out;
  }, [rawTicks, scale, width]);

  const onPointerDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    dragRef.current = { x: e.clientX, t0: scale.t0, t1: scale.t1 };
    (e.target as Element).setPointerCapture(e.pointerId);
  }, [scale]);
  const onPointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.clientX - drag.x;
    setScale(panScale({ t0: drag.t0, t1: drag.t1, widthPx: width }, -dx, bounds));
  }, [bounds, width]);
  const onPointerUp = useCallback(() => {
    dragRef.current = null;
  }, []);

  const doZoom = useCallback((factor: number) => {
    setScale((s) => zoomScale(s, factor, width / 2, bounds));
  }, [bounds, width]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") setScale((s) => panScale(s, width * 0.15, bounds));
    else if (e.key === "ArrowRight") setScale((s) => panScale(s, -width * 0.15, bounds));
    else if (e.key === "+" || e.key === "=") doZoom(1.3);
    else if (e.key === "-") doZoom(1 / 1.3);
    else return;
    e.preventDefault();
  }, [bounds, doZoom, width]);

  const gapFor = (times: number[]) => gapThresholdDays(times);

  return (
    <div className="rounded-lg border border-[#e7e2da] bg-white p-2">
      <div className="mb-1 flex items-center justify-between px-1">
        <p className="text-[11px] text-[#8a837b]">
          drag to pan · scroll or +/− to zoom · one row per orbit stack (direction + track)
        </p>
        <div className="flex gap-1">
          <button onClick={() => doZoom(1 / 1.3)} title="Zoom out" aria-label="Zoom out"
                  className="rounded border border-[#e7e2da] p-1 text-[#4a443c] hover:border-[#2c5f58]">
            <Minus size={12} aria-hidden />
          </button>
          <button onClick={() => doZoom(1.3)} title="Zoom in" aria-label="Zoom in"
                  className="rounded border border-[#e7e2da] p-1 text-[#4a443c] hover:border-[#2c5f58]">
            <Plus size={12} aria-hidden />
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <svg
          ref={svgRef}
          width={width}
          height={height}
          role="application"
          tabIndex={0}
          aria-label="Granule timeline; arrow keys pan, plus and minus zoom"
          onKeyDown={onKeyDown}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onWheel={(e) => {
            e.preventDefault();
            doZoom(e.deltaY < 0 ? 1.25 : 1 / 1.25);
          }}
          className="touch-none select-none"
          style={{ cursor: dragRef.current ? "grabbing" : "grab" }}
        >
          {/* ticks */}
          {tickList.map((t, i) => {
            const x = tToX({ ...scale, widthPx: width }, t.ms);
            return (
              <g key={i}>
                <line x1={x} y1={16} x2={x} y2={height} stroke={t.major ? "#d8d2c6" : "#f0ece4"} strokeWidth={1} />
                <text x={x + 2} y={11} fontSize={9} fill="#8a837b" className="mono">{t.label}</text>
              </g>
            );
          })}
          {/* today guideline */}
          {today > scale.t0 && today < scale.t1 && (
            <g>
              <line x1={tToX({ ...scale, widthPx: width }, today)} y1={16} x2={tToX({ ...scale, widthPx: width }, today)}
                    y2={height} stroke="#9e3b2c" strokeWidth={1} strokeDasharray="3 2" />
              <text x={tToX({ ...scale, widthPx: width }, today) + 2} y={height - 2} fontSize={9} fill="#9e3b2c">today</text>
            </g>
          )}
          {/* rows */}
          {rows.map((row, r) => {
            const y = 18 + r * ROW_H;
            const color = DIRECTION_COLORS[row.stack.direction ?? ""] ?? "#8a837b";
            const dirText = `${row.key} ${directionLabel(row.stack.direction)}`;
            const times = [...row.bars.map((b) => b.start), ...row.bars.map((b) => b.end), ...row.marks.map((m) => m.at)];
            const gapDays = gapFor(times);
            const items = [
              ...row.bars.map((b) => ({ at: b.start, item: b })),
              ...row.marks.map((m) => ({ at: m.at, item: m })),
            ].sort((a, b) => a.at - b.at);
            const px = (t: number) => tToX({ ...scale, widthPx: width }, t);
            return (
              <g key={row.key}>
                <rect x={0} y={y} width={width} height={ROW_H - 4} fill={r % 2 ? "#faf9f6" : "#ffffff"} />
                <text x={4} y={y + ROW_H / 2 - 2} fontSize={10} fontWeight={600} fill="#1c1a17">{row.key}</text>
                <text x={4} y={y + ROW_H / 2 + 8} fontSize={8} fill="#8a837b">{directionLabel(row.stack.direction)}</text>
                {row.bars.length === 0 && row.marks.length === 0 && (
                  <text x={LABEL_W} y={y + ROW_H / 2 + 3} fontSize={9} fill="#8a837b">
                    no dated acquisitions in this window
                  </text>
                )}
                {/* gap connectors with the EXACT day count */}
                {items.slice(1).map((cur, i) => {
                  const prev = items[i];
                  const gapD = (cur.at - prev.at) / DAY_MS;
                  if (!gapDays || gapD <= gapDays) return null;
                  const x1 = px(prev.at) + 10;
                  const x2 = px(cur.at) - 2;
                  if (x2 - x1 < 14) return null;
                  return (
                    <g key={`gap-${i}`}>
                      <line x1={x1} y1={y + ROW_H / 2 - 2} x2={x2} y2={y + ROW_H / 2 - 2}
                            stroke="#d8d2c6" strokeWidth={1} strokeDasharray="2 3" />
                      <text x={(x1 + x2) / 2} y={y + ROW_H / 2 - 5} fontSize={8.5} fill="#8a837b" textAnchor="middle">
                        {Math.round(gapD)} d gap
                      </text>
                    </g>
                  );
                })}
                {row.bars.map((b) => {
                  const selected = props.selection?.kind === "gunw" && props.selection.stackKey === row.key
                    && props.selection.index === b.index;
                  const x1 = px(b.start);
                  const x2 = Math.max(px(b.end), px(b.start) + 3);
                  return (
                    <g key={`b-${b.index}`}>
                      <rect
                        x={x1} y={y + 2} width={Math.max(3, x2 - x1)} height={ROW_H - 8} rx={3}
                        fill={color} fillOpacity={selected ? 1 : 0.75}
                        stroke={selected ? "#1c1a17" : "none"} strokeWidth={selected ? 1.5 : 0}
                        tabIndex={0}
                        role="button"
                        aria-label={`pair ${new Date(b.start).toISOString().slice(0, 10)} to ${new Date(b.end).toISOString().slice(0, 10)} baseline ${Math.round((b.end - b.start) / DAY_MS)} days`}
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => {
                          e.stopPropagation();
                          props.onSelect({ stackKey: row.key, kind: "gunw", index: b.index });
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            props.onSelect({ stackKey: row.key, kind: "gunw", index: b.index });
                            e.preventDefault();
                          }
                        }}
                        className="cursor-pointer"
                      >
                        <title>{`${dirText}: pair ${new Date(b.start).toISOString().slice(0, 10)} -> ${new Date(b.end).toISOString().slice(0, 10)} (${Math.round((b.end - b.start) / DAY_MS)} d)`}</title>
                      </rect>
                    </g>
                  );
                })}
                {row.marks.map((m) => {
                  const selected = props.selection?.kind === "gcov" && props.selection.stackKey === row.key
                    && props.selection.index === m.index;
                  return (
                    <circle
                      key={`m-${m.index}`}
                      cx={px(m.at)} cy={y + ROW_H / 2 - 2} r={selected ? 5 : 4}
                      fill={color} fillOpacity={selected ? 1 : 0.75}
                      stroke={selected ? "#1c1a17" : "none"} strokeWidth={selected ? 1.5 : 0}
                      tabIndex={0}
                      role="button"
                      aria-label={`acquisition ${new Date(m.at).toISOString().slice(0, 10)}`}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        props.onSelect({ stackKey: row.key, kind: "gcov", index: m.index });
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          props.onSelect({ stackKey: row.key, kind: "gcov", index: m.index });
                          e.preventDefault();
                        }
                      }}
                      className="cursor-pointer"
                    >
                      <title>{`${dirText}: acquisition ${new Date(m.at).toISOString().slice(0, 10)}`}</title>
                    </circle>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
      <p className="mt-1 px-1 text-[10px] text-[#8a837b]">
        visible window {new Date(scale.t0).toISOString().slice(0, 10)} → {new Date(scale.t1).toISOString().slice(0, 10)}
        {" · "}scale {pxPerMs({ ...scale, widthPx: width }).toExponential(1)} px/ms
      </p>
    </div>
  );
}

export function selectionFromPath(data: GranuleSearchResponse, sel: TimelineSelection | null) {
  if (!sel) return null;
  const stack = data.stacks[sel.stackKey];
  if (!stack) return null;
  if (sel.kind === "gunw") {
    const pair = stack.gunw[sel.index];
    return pair ? { kind: "gunw" as const, pair, stackKey: sel.stackKey, stack } : null;
  }
  const epoch = stack.gcov[sel.index];
  return epoch ? { kind: "gcov" as const, epoch, stackKey: sel.stackKey, stack } : null;
}
