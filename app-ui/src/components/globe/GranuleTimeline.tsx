"use client";

/** GranuleTimeline (§7.5) — a TRUE continuous UTC time scale (lib/timescale.ts, DEC11: no chart
 *  library), one row per stack (direction + track, the engine's own grouping), one bar per GUNW
 *  PairGroup (ref -> sec) and one marker per GCOV EpochGroup. Gaps carry the EXACT day count
 *  (never a judgement word); the gap threshold derives from the row's own median spacing.
 *  Pan (drag) / zoom (wheel, buttons) / keyboard operable; "today" guideline; ticks adapt. */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
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
const LANE_H = 18;      // one bar lane
const MARK_H = 12;      // the strip for GCOV dots (never shares a lane with a bar)
const ROW_PAD = 8;      // breathing room above/below a row's lanes
const LABEL_W = 64;     // fixed left gutter for the stack label; the plot never draws under it
const AXIS_H = 18;

export interface GranuleTimelineProps {
  /** pairs the verdict analysed / is based on (from the engine's result); marked on the bars (B9) */
  verdictUse?: { stack: string; ref_time: string; sec_time: string; selected: boolean }[];
  data: GranuleSearchResponse;
  selection: TimelineSelection | null;
  onSelect: (sel: TimelineSelection | null) => void;
  onWindowChange: (fromIso: string, toIso: string) => void;
}

interface Row {
  key: string;
  stack: GranuleStack;
  bars: { start: number; end: number; kind: "gunw"; index: number; lane: number }[];
  marks: { at: number; kind: "gcov"; index: number }[];
  /** number of bar lanes (>= 1): overlapping pairs in one stack get their own lane instead of hiding each other */
  lanes: number;
  /** pixel geometry, filled by layoutRows */
  y: number;
  h: number;
}

/** greedy interval packing: a bar goes in the first lane whose last bar has ended (touching ends share a lane —
 *  a chain of ref→sec pairs stays on one line, only truly overlapping pairs stack). */
function packLanes(bars: { start: number; end: number }[]): number[] {
  const order = bars.map((_, i) => i).sort((a, b) => bars[a].start - bars[b].start || bars[a].end - bars[b].end);
  const laneEnds: number[] = [];
  const out: number[] = new Array(bars.length).fill(0);
  for (const i of order) {
    let lane = laneEnds.findIndex((end) => end <= bars[i].start);
    if (lane < 0) { lane = laneEnds.length; laneEnds.push(0); }
    laneEnds[lane] = bars[i].end;
    out[i] = lane;
  }
  return out;
}

const rowHeight = (r: { lanes: number; marks: unknown[] }) =>
  ROW_PAD + (r.marks.length ? MARK_H : 0) + Math.max(1, r.lanes) * LANE_H;

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
      if (Number.isFinite(start) && Number.isFinite(end)) bars.push({ start: Math.min(start, end), end: Math.max(start, end), kind: "gunw", index, lane: 0 });
    });
    stack.gcov.forEach((e, index) => {
      const at = e.time ? Date.parse(e.time) : NaN;
      if (Number.isFinite(at)) marks.push({ at, kind: "gcov", index });
    });
    const lanes = packLanes(bars);
    bars.forEach((b, i) => { b.lane = lanes[i]; });
    const row: Row = { key, stack, bars, marks, lanes: Math.max(1, ...lanes.map((l) => l + 1)), y: 0, h: 0 };
    row.h = rowHeight(row);
    rows.push(row);
  }
  let y = AXIS_H;
  for (const row of rows) { row.y = y; y += row.h; }
  return rows;
}

export default function GranuleTimeline(props: GranuleTimelineProps) {
  const bounds = useMemo(() => ({
    min: Date.parse(props.data.window.t0),
    max: Date.parse(props.data.window.t1),
  }), [props.data.window.t0, props.data.window.t1]);
  const [scale, setScale] = useState<TimeScale>({ t0: bounds.min, t1: bounds.max, widthPx: 640 - LABEL_W });
  const [width, setWidth] = useState(640);          // the whole svg; the plot is `width - LABEL_W` wide
  const plotW = Math.max(120, width - LABEL_W);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{ x: number; t0: number; t1: number } | null>(null);
  const lastWindow = useRef<string>("");
  const clipId = `tl-clip-${useId().replace(/:/g, "")}`;

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const w = Math.max(280, entries[0]?.contentRect.width ?? 640);
      setWidth(w);
      setScale((s) => ({ ...s, widthPx: Math.max(120, w - LABEL_W) }));
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
  const rowsBottom = rows.length ? rows[rows.length - 1].y + rows[rows.length - 1].h : AXIS_H;
  const height = rowsBottom + 14;
  const today = Date.now();
  const sc = useMemo<TimeScale>(() => ({ ...scale, widthPx: plotW }), [scale, plotW]);
  const px = useCallback((ms: number) => tToX(sc, ms), [sc]);   // plot coordinates (0 = left edge of the plot)
  const rawTicks = useMemo(() => ticksFor(sc), [sc]);
  // §8.3 critique fix: drop labels that would overlap (month boundary + week tick 3px apart
  // rendered "1 Jun 20263 Jul 2026"); a nearby major (month) label replaces a colliding minor.
  const tickList = useMemo(() => {
    const wOf = (t: (typeof rawTicks)[number]) => t.label.length * 5.7 + 8; // ~mono 9px/char + x offset
    const out: typeof rawTicks = [];
    for (const t of rawTicks) {
      const x = tToX(sc, t.ms);
      if (x < 0) continue; // label would start off-canvas
      const last = out[out.length - 1];
      if (last && x < tToX(sc, last.ms) + wOf(last)) {
        if (t.major && !last.major && x - tToX(sc, last.ms) < 18) out[out.length - 1] = t;
        continue;
      }
      out.push(t);
    }
    return out;
  }, [rawTicks, sc]);

  const onPointerDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    dragRef.current = { x: e.clientX, t0: scale.t0, t1: scale.t1 };
    (e.target as Element).setPointerCapture(e.pointerId);
  }, [scale]);
  const onPointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.clientX - drag.x;
    setScale(panScale({ t0: drag.t0, t1: drag.t1, widthPx: plotW }, -dx, bounds));
  }, [bounds, plotW]);
  const onPointerUp = useCallback(() => {
    dragRef.current = null;
  }, []);

  const doZoom = useCallback((factor: number) => {
    setScale((s) => zoomScale(s, factor, plotW / 2, bounds));
  }, [bounds, plotW]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") setScale((s) => panScale(s, plotW * 0.15, bounds));
    else if (e.key === "ArrowRight") setScale((s) => panScale(s, -plotW * 0.15, bounds));
    else if (e.key === "+" || e.key === "=") doZoom(1.3);
    else if (e.key === "-") doZoom(1 / 1.3);
    else return;
    e.preventDefault();
  }, [bounds, doZoom, plotW]);

  const gapFor = (times: number[]) => gapThresholdDays(times);
  const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);

  return (
    <div className="rounded-lg border border-[#e7e2da] bg-white p-2">
      <div className="mb-1 flex items-center justify-between gap-2 px-1">
        <p className="text-[11px] text-[#8a837b]">
          drag to pan · scroll or +/− to zoom · one row per orbit stack (direction + track); overlapping pairs stack in lanes
        </p>
        <div className="flex shrink-0 gap-1">
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
          width="100%"
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
          style={{ cursor: dragRef.current ? "grabbing" : "grab", display: "block" }}
        >
          <defs>
            <clipPath id={clipId}><rect x={LABEL_W} y={0} width={plotW} height={height} /></clipPath>
          </defs>

          {/* row backgrounds (full width, behind everything) */}
          {rows.map((row, r) => (
            <rect key={`bg-${row.key}`} x={0} y={row.y} width={width} height={row.h - 2} fill={r % 2 ? "#faf9f6" : "#ffffff"} />
          ))}

          {/* the plot: everything time-based lives here, clipped so nothing can draw under the label gutter */}
          <g clipPath={`url(#${clipId})`}>
            <g transform={`translate(${LABEL_W},0)`}>
              {/* ticks */}
              {tickList.map((t, i) => {
                const x = px(t.ms);
                return (
                  <g key={i}>
                    <line x1={x} y1={AXIS_H - 2} x2={x} y2={height} stroke={t.major ? "#d8d2c6" : "#f0ece4"} strokeWidth={1} />
                    <text x={x + 2} y={11} fontSize={9} fill="#8a837b" className="mono">{t.label}</text>
                  </g>
                );
              })}
              {/* today guideline */}
              {today > scale.t0 && today < scale.t1 && (
                <g>
                  <line x1={px(today)} y1={AXIS_H - 2} x2={px(today)} y2={height} stroke="#9e3b2c" strokeWidth={1} strokeDasharray="3 2" />
                  <text x={px(today) + 2} y={height - 3} fontSize={9} fill="#9e3b2c">today</text>
                </g>
              )}
              {/* rows */}
              {rows.map((row) => {
                const color = DIRECTION_COLORS[row.stack.direction ?? ""] ?? "#8a837b";
                const dirText = `${row.key} ${directionLabel(row.stack.direction)}`;
                const times = [...row.bars.map((b) => b.start), ...row.bars.map((b) => b.end), ...row.marks.map((m) => m.at)];
                const gapDays = gapFor(times);
                const items = [
                  ...row.bars.map((b) => ({ at: b.start, to: b.end })),
                  ...row.marks.map((m) => ({ at: m.at, to: m.at })),
                ].sort((a, b) => a.at - b.at);
                const markY = row.y + ROW_PAD / 2 + MARK_H / 2;
                const lanesTop = row.y + ROW_PAD / 2 + (row.marks.length ? MARK_H : 0);
                const midY = lanesTop + (Math.max(1, row.lanes) * LANE_H) / 2;
                // gap connectors carry the EXACT day count between consecutive acquisitions; the line is only drawn
                // through space that is empty in EVERY lane (after the furthest end seen so far)
                let covered = -Infinity;
                const gaps: { x1: number; x2: number; days: number }[] = [];
                items.forEach((cur, i) => {
                  if (i > 0 && gapDays) {
                    const gapD = (cur.at - items[i - 1].at) / DAY_MS;
                    const x1 = px(covered) + 5;          // covered >= the previous item's end: start after the furthest bar/dot
                    const x2 = px(cur.at) - 2;
                    if (gapD > gapDays && cur.at > covered && x2 - x1 >= 14) gaps.push({ x1, x2, days: gapD });
                  }
                  covered = Math.max(covered, cur.to);
                });
                return (
                  <g key={row.key}>
                    {row.bars.length === 0 && row.marks.length === 0 && (
                      <text x={8} y={midY + 3} fontSize={9} fill="#8a837b">
                        no dated acquisitions in this window
                      </text>
                    )}
                    {gaps.map((g, i) => (
                      <g key={`gap-${i}`}>
                        <line x1={g.x1} y1={midY} x2={g.x2} y2={midY} stroke="#d8d2c6" strokeWidth={1} strokeDasharray="2 3" />
                        <text x={(g.x1 + g.x2) / 2} y={midY - 3} fontSize={8.5} fill="#8a837b" textAnchor="middle">
                          {Math.round(g.days)} d gap
                        </text>
                      </g>
                    ))}
                    {row.bars.map((b) => {
                      const selected = props.selection?.kind === "gunw" && props.selection.stackKey === row.key
                        && props.selection.index === b.index;
                      const x1 = px(b.start);
                      const x2 = Math.max(px(b.end), px(b.start) + 3);
                      const by = lanesTop + b.lane * LANE_H + 1;
                      const bh = LANE_H - 4;
                      const used = (props.verdictUse ?? []).find((u) => u.stack === row.key
                        && u.ref_time.slice(0, 10) === isoDay(b.start)
                        && u.sec_time.slice(0, 10) === isoDay(b.end));
                      return (
                        <g key={`b-${b.index}`}>
                          <rect
                            x={x1} y={by} width={Math.max(3, x2 - x1)} height={bh} rx={3}
                            fill={color} fillOpacity={selected ? 1 : 0.75}
                            stroke={selected ? "#1c1a17" : "#ffffff"} strokeWidth={selected ? 1.5 : 0.75}
                            tabIndex={0}
                            role="button"
                            aria-label={`pair ${isoDay(b.start)} to ${isoDay(b.end)} baseline ${Math.round((b.end - b.start) / DAY_MS)} days`}
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
                            <title>{`${dirText}: pair ${isoDay(b.start)} -> ${isoDay(b.end)} (${Math.round((b.end - b.start) / DAY_MS)} d)`}</title>
                          </rect>
                          {used && (
                            // drawn INSIDE the bar (its own lane, so it can never collide with a neighbour) and click-through
                            <text x={(x1 + x2) / 2} y={by + bh - 3} fontSize={10} textAnchor="middle" pointerEvents="none"
                                  fill={used.selected ? "#ffd166" : "#ffffff"}
                                  data-testid={used.selected ? "verdict-selected-pair" : "verdict-analysed-pair"}>
                              <title>{used.selected ? "the pair the verdict is based on" : "analysed by the verdict, not the one it is based on"}</title>
                              {used.selected ? "★" : "·"}
                            </text>
                          )}
                        </g>
                      );
                    })}
                    {row.marks.map((m) => {
                      const selected = props.selection?.kind === "gcov" && props.selection.stackKey === row.key
                        && props.selection.index === m.index;
                      return (
                        <circle
                          key={`m-${m.index}`}
                          cx={px(m.at)} cy={markY} r={selected ? 5 : 4}
                          fill={color} fillOpacity={selected ? 1 : 0.75}
                          stroke={selected ? "#1c1a17" : "#ffffff"} strokeWidth={selected ? 1.5 : 0.75}
                          tabIndex={0}
                          role="button"
                          aria-label={`acquisition ${isoDay(m.at)}`}
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
                          <title>{`${dirText}: acquisition ${isoDay(m.at)}`}</title>
                        </circle>
                      );
                    })}
                  </g>
                );
              })}
            </g>
          </g>

          {/* label gutter: solid background so a bar scrolling left slides UNDER it, never over the stack name */}
          {rows.map((row, r) => (
            <g key={`label-${row.key}`}>
              <rect x={0} y={row.y} width={LABEL_W} height={row.h - 2} fill={r % 2 ? "#faf9f6" : "#ffffff"} />
              <line x1={LABEL_W} y1={row.y} x2={LABEL_W} y2={row.y + row.h - 2} stroke="#e7e2da" strokeWidth={1} />
              <text x={4} y={row.y + row.h / 2 - 2} fontSize={10} fontWeight={600} fill="#1c1a17">{row.key}</text>
              <text x={4} y={row.y + row.h / 2 + 9} fontSize={8} fill="#8a837b">{directionLabel(row.stack.direction)}</text>
            </g>
          ))}
        </svg>
      </div>
      <p className="mt-1 px-1 text-[10px] text-[#8a837b]">
        visible window {new Date(scale.t0).toISOString().slice(0, 10)} → {new Date(scale.t1).toISOString().slice(0, 10)}
        {" · "}scale {pxPerMs(sc).toExponential(1)} px/ms
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
