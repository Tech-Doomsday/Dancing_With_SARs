"use client";

/** TimelineCanvas (§7.8) — the Explorer's instrument timeline on <canvas> (DEC12):
 *  - real UTC scale: adaptive tick levels (years → months → weeks → days → 6 h/hours), major
 *    labels, vertical gridlines, a "now" marker, hover crosshair with the exact UTC timestamp;
 *  - one lane group per stack (direction + track; ascending first), ascending cyan, descending
 *    violet (AA on the dark background; text labels always accompany the colours);
 *  - GUNW bars = reference → secondary with dots at both acquisitions; GCOV = a dot/tick at its
 *    time (minimum visible width); greedy-packed sub-lanes (deterministic, no overlap);
 *  - explicit day gaps: a thin bracket labelled "Nd" between consecutive engine epochs where
 *    there is room; amber only when the engine supplies nominal_repeat_days (it does not —
 *    so amber never renders, and that is the honest state);
 *  - brush minimap (full range + density) to pan/zoom; wheel zoom anchored at the cursor;
 *    drag pan; double-click a bar to zoom to it; keys: ←/→ pan, +/− zoom, ↑/↓ roving bar
 *    focus, Enter select, Home fit-all;
 *  - selection = amber outline (shift/ctrl extends); DPR-aware; resize-safe; interval-index
 *    hit-testing (sorted starts + binary search) so ≥5,000 bars pan at interactive rates.
 *  Time is UTC only, stated on screen. All gap values come from the engine response. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BackgroundGroup, ExplorerGranule, ExplorerStack, GranuleExplorerResponse } from "@/lib/types";
import { groupTimes } from "@/lib/groups";
import { clamp, pan as panScale, pxPerMs, ticks as ticksFor, tToX, xToT, zoom as zoomScale, type TimeScale } from "@/lib/timescale";
import { packLanes, type BarInterval } from "@/lib/explorer";

const DAY_MS = 86_400_000;
const AXIS_H = 30;
const STACK_HEADER_H = 20;
const LANE_H = 22;
const BRUSH_H = 56;

const CYAN = "#3fb6c9";
const VIOLET = "#b39ce8";
const AMBER = "#e6c977";
const GRID = "#1c222b";
const GRID_MAJOR = "#2a323e";
const INK = "#d9dee4";
const MUTED = "#8b949e";

export interface PackedGranuleBar {
  id: string;                 // the granule id (first variant when bars come from groups)
  stackKey: string;
  kind: "gunw" | "gcov" | "goff" | "unknown";
  start: number;
  end: number;
  lane: number;
  granule: ExplorerGranule;
  /** §4.2: engine-computed acquisition group behind this bar (one bar per group) */
  group?: BackgroundGroup;
}

export interface PackedHit {
  bar: PackedGranuleBar;
  /** §4.4: the pair END the pointer is on (within the end-dot tolerance) — null otherwise */
  end: "ref" | "sec" | null;
}

interface Props {
  data: GranuleExplorerResponse;
  granules: ExplorerGranule[];
  stacks: ExplorerStack[];
  groups?: BackgroundGroup[];
  selectedIds: string[];
  focusedId: string | null;
  onSelect: (id: string, extend: boolean) => void;
  onFocus: (id: string | null) => void;
  onViewWindow: (w: { t0: number; t1: number }) => void;
  /** §4.4: clicking a pair bar's END opens the Compare view for that end */
  onEndClick?: (groupId: string, end: "ref" | "sec") => void;
}

function fmtUtcFull(ms: number): string {
  return new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function fmtSpan(ms: number): string {
  const d = new Date(ms);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())} ${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`;
}

export default function TimelineCanvas(props: Props) {
  const bounds = useMemo(() => ({
    min: Date.parse(props.data.window.t0),
    max: Date.parse(props.data.window.t1),
  }), [props.data.window.t0, props.data.window.t1]);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const axisRef = useRef<HTMLCanvasElement | null>(null);
  const lanesRef = useRef<HTMLCanvasElement | null>(null);
  const brushRef = useRef<HTMLCanvasElement | null>(null);
  const [width, setWidth] = useState(900);
  const [scale, setScale] = useState<TimeScale>({ t0: bounds.min, t1: bounds.max, widthPx: 900 });
  const [hover, setHover] = useState<{ x: number; y: number; t: number; bar: PackedGranuleBar | null; end: "ref" | "sec" | null } | null>(null);
  const dragRef = useRef<{ x: number; t0: number; t1: number } | null>(null);
  const brushDragRef = useRef<{ x: number; t0: number; t1: number } | null>(null);
  const needDraw = useRef(true);

  // ---- data → packed bars per stack (memoized; deterministic order) ----------------------------
  // §4.2: when the engine answered with groups, ONE bar is drawn per acquisition group (the
  // first variant's id carries selection); otherwise the per-granule shape is kept (older
  // fixtures / an engine without groups). Granules/groups without a verified time are not
  // drawn (no dates to place — the honest absence), matching the previous behaviour.
  const stackLayout = useMemo(() => {
    const byStack = new Map<string, ExplorerGranule[]>();
    for (const g of props.granules) {
      const list = byStack.get(g.stack) ?? [];
      list.push(g);
      byStack.set(g.stack, list);
    }
    const useGroups = (props.groups?.length ?? 0) > 0;
    const groupOf = new Map<string, BackgroundGroup>();
    if (useGroups) {
      for (const grp of props.groups!) {
        for (const gid of grp.granule_ids) groupOf.set(gid, grp);
      }
    }
    const groups: {
      stack: ExplorerStack;
      rows: PackedGranuleBar[][];
      yTop: number;
      height: number;
    }[] = [];
    let y = AXIS_H;
    for (const stack of props.stacks) {
      const members = byStack.get(stack.key) ?? [];
      const intervals: BarInterval[] = [];
      const kindOf = new Map<string, "gunw" | "gcov" | "goff" | "unknown">();
      if (useGroups) {
        for (const grp of props.groups!) {
          if (grp.stack !== stack.key) continue;
          const times = groupTimes(grp);
          if (!times) continue;
          const first = grp.variants[0] ?? props.granules.find((g) => g.id === grp.granule_ids[0]);
          if (!first) continue;
          intervals.push({ key: first.id, start: times.start, end: times.end });
          kindOf.set(first.id, grp.kind === "pair"
            ? (grp.product === "GOFF" ? "goff" : "gunw")
            : grp.kind === "single" ? "gcov" : "unknown");
        }
      } else {
        for (const g of members) {
          if (g.ref_time && g.sec_time) {
            const a = Date.parse(g.ref_time);
            const b = Date.parse(g.sec_time);
            if (Number.isFinite(a) && Number.isFinite(b)) {
              intervals.push({ key: g.id, start: Math.min(a, b), end: Math.max(a, b) });
              kindOf.set(g.id, g.product === "GCOV" ? "gcov" : g.product === "GOFF" ? "goff" : "gunw");
            }
          } else if (g.start) {
            const a = Date.parse(g.start);
            if (Number.isFinite(a)) {
              intervals.push({ key: g.id, start: a, end: a });
              kindOf.set(g.id, "gcov");
            }
          }
        }
      }
      // pack with a 1-px minimum gap (converted to ms once the width is known; 0.5 px here and
      // the draw side enforces the visual minimum) — packLanes is deterministic
      const packed = packLanes(intervals, 0);
      const nLanes = Math.max(1, new Set(packed.map((p) => p.lane)).size);
      const rows: PackedGranuleBar[][] = Array.from({ length: nLanes }, () => []);
      for (const p of packed) {
        rows[p.lane].push({
          id: p.key,
          stackKey: stack.key,
          kind: kindOf.get(p.key) ?? "gunw",
          start: p.start,
          end: p.end,
          lane: p.lane,
          granule: members.find((m) => m.id === p.key)
            ?? props.granules.find((g) => g.id === p.key)
            ?? (useGroups ? props.groups!.find((grp) => grp.granule_ids.includes(p.key))?.variants[0] : undefined)!,
          group: groupOf.get(p.key),
        });
      }
      for (const row of rows) row.sort((a, b) => a.start - b.start);
      const height = STACK_HEADER_H + nLanes * LANE_H;
      groups.push({ stack, rows, yTop: y, height });
      y += height;
    }
    return { groups, contentHeight: Math.max(y, AXIS_H + LANE_H) };
  }, [props.granules, props.stacks, props.groups]);

  const flatBars = useMemo(() => {
    const out: PackedGranuleBar[] = [];
    for (const g of stackLayout.groups) for (const row of g.rows) out.push(...row);
    return out;
  }, [stackLayout]);

  const barsByStart = useMemo(
    () => [...flatBars].sort((a, b) => a.start - b.start),
    [flatBars],
  );

  // ---- size (ResizeObserver; DPR handled at draw time) -----------------------------------------
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const w = Math.max(320, Math.floor((entries[0]?.contentRect.width ?? 900)));
      setWidth(w);
      setScale((s) => ({ ...s, widthPx: w }));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // keep the window inside bounds when the data changes
  useEffect(() => {
    setScale((s) => clamp({ ...s }, bounds));
  }, [bounds]);

  // report the visible window upward (for "only in view")
  useEffect(() => {
    props.onViewWindow({ t0: scale.t0, t1: scale.t1 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scale.t0, scale.t1]);

  // ---- the draw loop (rAF, only when invalidated) ------------------------------------------------
  useEffect(() => {
    needDraw.current = true;                       // every dep change (scale/hover/selection) redraws
    let raf = 0;
    const draw = () => {
      if (needDraw.current) {
        needDraw.current = false;
        drawAll();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scale, width, hover, props.selectedIds, props.focusedId, stackLayout, props.data]);

  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  const hoverRef = useRef(hover);
  hoverRef.current = hover;
  const propsRef = useRef(props);
  propsRef.current = props;
  const stackLayoutRef = useRef(stackLayout);
  stackLayoutRef.current = stackLayout;

  const drawAll = useCallback(() => {
    const s = scaleRef.current;
    const p = propsRef.current;
    const layout = stackLayoutRef.current;
    const dpr = Math.min(2.5, window.devicePixelRatio || 1);

    // ---- axis canvas ----
    const axis = axisRef.current;
    if (axis) {
      axis.width = Math.floor(width * dpr);
      axis.height = Math.floor(AXIS_H * dpr);
      axis.style.width = `${width}px`;
      axis.style.height = `${AXIS_H}px`;
      const ctx = axis.getContext("2d");
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, AXIS_H);
        ctx.font = "9px ui-monospace, monospace";
        const tickList = ticksFor({ ...s, widthPx: width });
        let lastLabelEnd = -1;
        for (const t of tickList) {
          const x = tToX({ ...s, widthPx: width }, t.ms);
          if (x < -40 || x > width + 40) continue;
          ctx.strokeStyle = t.major ? GRID_MAJOR : GRID;
          ctx.beginPath();
          ctx.moveTo(x, t.major ? 12 : 18);
          ctx.lineTo(x, AXIS_H);
          ctx.stroke();
          // label collision culling (a major label replaces a colliding minor, same as the Data tab)
          const w = t.label.length * 5.6 + 4;
          if (x + 2 < lastLabelEnd) continue;
          ctx.fillStyle = t.major ? INK : MUTED;
          ctx.textAlign = "left";
          ctx.fillText(t.label, x + 2, t.major ? 9 : 15);
          lastLabelEnd = x + 2 + w;
        }
        ctx.fillStyle = MUTED;
        ctx.textAlign = "right";
        ctx.fillText("UTC", width - 4, AXIS_H - 4);
        // the "now" marker on the axis
        const now = Date.now();
        if (now >= s.t0 && now <= s.t1) {
          const x = tToX({ ...s, widthPx: width }, now);
          ctx.strokeStyle = "#e07856";
          ctx.setLineDash([3, 2]);
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, AXIS_H);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }

    // ---- lanes canvas ----
    const lanes = lanesRef.current;
    if (lanes) {
      lanes.width = Math.floor(width * dpr);
      lanes.height = Math.floor(layout.contentHeight * dpr);
      lanes.style.width = `${width}px`;
      lanes.style.height = `${layout.contentHeight}px`;
      const ctx = lanes.getContext("2d");
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, layout.contentHeight);
        const w = width;
        const px = (t: number) => tToX({ ...s, widthPx: w }, t);
        const tickList = ticksFor({ ...s, widthPx: w });
        // gridlines
        for (const t of tickList) {
          const x = px(t.ms);
          if (x < 0 || x > w) continue;
          ctx.strokeStyle = t.major ? GRID_MAJOR : GRID;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, layout.contentHeight);
          ctx.stroke();
        }
        // "now" marker
        const now = Date.now();
        if (now >= s.t0 && now <= s.t1) {
          const x = px(now);
          ctx.strokeStyle = "#e07856";
          ctx.setLineDash([3, 2]);
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, layout.contentHeight);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        for (const group of layout.groups) {
          const { stack, rows, yTop, height } = group;
          const color = stack.direction === "D" ? VIOLET : stack.direction === "A" ? CYAN : MUTED;
          // stack band
          ctx.fillStyle = "#12161d";
          ctx.fillRect(0, yTop, w, height);
          // header: stack label + counts + median gap (all engine-supplied)
          ctx.font = "10px ui-monospace, monospace";
          ctx.fillStyle = color;
          ctx.textAlign = "left";
          ctx.fillText(stack.key, 4, yTop + 13);
          ctx.fillStyle = MUTED;
          const bits = [
            stack.direction === "D" ? "descending" : stack.direction === "A" ? "ascending" : "direction unknown",
            `${stack.n_granules} granules`,
            `${stack.n_epochs} epochs`,
            stack.median_gap_days != null
              ? `gap ${stack.min_gap_days}/${stack.median_gap_days}/${stack.max_gap_days} d`
              : null,
          ].filter(Boolean) as string[];
          ctx.fillText(bits.join(" · "), 40, yTop + 13);

          // day-gap brackets between consecutive engine epochs (drawn at the header line)
          const epochs = stack.epochs.map((e) => Date.parse(e)).filter(Number.isFinite) as number[];
          for (let i = 1; i < epochs.length; i++) {
            const x1 = px(epochs[i - 1]);
            const x2 = px(epochs[i]);
            if (x2 - x1 < 26) continue; // no room — the summary strip carries the numbers
            const yb = yTop + height - 3;
            ctx.strokeStyle = GRID_MAJOR;
            ctx.beginPath();
            ctx.moveTo(x1, yb - 3);
            ctx.lineTo(x1, yb);
            ctx.lineTo(x2, yb);
            ctx.lineTo(x2, yb - 3);
            ctx.stroke();
            ctx.fillStyle = stack.nominal_repeat_days != null && stack.gaps_days[i - 1] > stack.nominal_repeat_days
              ? AMBER
              : MUTED;
            ctx.textAlign = "center";
            ctx.fillText(`${Math.round(stack.gaps_days[i - 1])}d`, (x1 + x2) / 2, yb - 6);
            ctx.textAlign = "left";
          }

          // bars
          rows.forEach((row, lane) => {
            const yBar = yTop + STACK_HEADER_H + lane * LANE_H + 5;
            for (const bar of row) {
              const x1 = px(bar.start);
              const x2 = Math.max(px(bar.end), x1 + 3);
              if (x2 < -8 || x1 > w + 8) continue;
              const selected = p.selectedIds.includes(bar.id);
              const focused = p.focusedId === bar.id;
              const hovered = hoverRef.current?.bar?.id === bar.id;
              const hoverEnd = hoverRef.current?.bar?.id === bar.id ? hoverRef.current?.end : null;
              ctx.globalAlpha = selected || hovered || focused ? 1 : 0.82;
              ctx.fillStyle = color;
              if (bar.kind === "unknown") {
                // an id that matches no layout: a hollow diamond, never a invented pair/dot shape
                const cx = x1 + 1.5, cy = yBar + LANE_H / 2 - 5;
                ctx.beginPath();
                ctx.moveTo(cx, cy - 4); ctx.lineTo(cx + 4, cy); ctx.lineTo(cx, cy + 4); ctx.lineTo(cx - 4, cy);
                ctx.closePath();
                ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.stroke(); ctx.lineWidth = 1;
              } else if (bar.kind === "gcov") {
                // a dot/tick at its time (minimum visible width)
                ctx.beginPath();
                ctx.arc(x1 + 1.5, yBar + LANE_H / 2 - 5, 3.5, 0, Math.PI * 2);
                ctx.fill();
              } else {
                const yH = LANE_H - 12;
                ctx.fillRect(x1, yBar, Math.max(2, x2 - x1), yH);
                // dots at both acquisition times (the §4.4 compare entries)
                ctx.fillStyle = "#0e1116";
                ctx.beginPath();
                ctx.arc(x1 + 1.5, yBar + yH / 2, 2, 0, Math.PI * 2);
                ctx.fill();
                ctx.beginPath();
                ctx.arc(Math.max(x1 + 1.5, x2 - 1.5), yBar + yH / 2, 2, 0, Math.PI * 2);
                ctx.fill();
                if (hoverEnd) {
                  // the END the pointer is on gets a ring — it opens the Compare view (§4.4)
                  ctx.strokeStyle = AMBER;
                  ctx.lineWidth = 1.6;
                  ctx.beginPath();
                  ctx.arc(hoverEnd === "ref" ? x1 + 1.5 : Math.max(x1 + 1.5, x2 - 1.5), yBar + yH / 2, 4, 0, Math.PI * 2);
                  ctx.stroke();
                  ctx.lineWidth = 1;
                }
              }
              // §4.2: the variant badge — "×N" right of the bar when the engine grouped variants
              const nV = bar.group?.n_variants ?? 1;
              if (nV > 1) {
                ctx.globalAlpha = 1;
                ctx.fillStyle = AMBER;
                ctx.font = "9px ui-monospace, monospace";
                ctx.textAlign = "left";
                ctx.fillText(`×${nV}`, x2 + 4, bar.kind === "gcov" ? yBar + LANE_H / 2 - 2 : yBar + LANE_H - 9);
              }
              ctx.globalAlpha = 1;
              if (selected || focused || hovered) {
                ctx.strokeStyle = selected ? AMBER : focused ? "#f2efe9" : INK;
                ctx.lineWidth = selected || focused ? 1.6 : 1;
                ctx.strokeRect(x1 - 2, yBar - 2, Math.max(4, x2 - x1) + 4, LANE_H - 8);
                ctx.lineWidth = 1;
              }
            }
          });
        }

        // hover crosshair + tooltip
        const h = hoverRef.current;
        if (h) {
          ctx.strokeStyle = "#3a4450";
          ctx.beginPath();
          ctx.moveTo(h.x, 0);
          ctx.lineTo(h.x, layout.contentHeight);
          ctx.stroke();
          ctx.fillStyle = MUTED;
          ctx.font = "9px ui-monospace, monospace";
          ctx.textAlign = h.x > width - 130 ? "right" : "left";
          ctx.fillText(fmtUtcFull(h.t), h.x + (h.x > width - 130 ? -4 : 4), 10);
          if (h.bar) {
            const g = h.bar.granule;
            const nV = h.bar.group?.n_variants ?? 1;
            const kindText = h.bar.kind === "gunw" ? "pair (two dates)"
              : h.bar.kind === "goff" ? "offset pair (two dates)"
              : h.bar.kind === "gcov" ? "single date"
              : "unknown layout (raw)";
            const lines = [
              `${g.id.slice(0, 44)}${g.id.length > 44 ? "…" : ""}`,
              `${g.stack} · ${g.product ?? "?"} · ${kindText}${nV > 1 ? ` · ×${nV} variants` : ""}` +
                (h.end ? ` · click to compare the ${h.end === "ref" ? "reference" : "secondary"} end` : ""),
              g.ref_time ? `ref ${fmtSpan(Date.parse(g.ref_time))}` : g.start ? `start ${fmtSpan(Date.parse(g.start))}` : "undated",
              g.sec_time ? `sec ${fmtSpan(Date.parse(g.sec_time))}` : null,
              g.baseline_days != null ? `baseline ${g.baseline_days} d` : null,
            ].filter(Boolean) as string[];
            const boxW = 300;
            const boxH = lines.length * 12 + 8;
            const bx = Math.min(width - boxW - 4, Math.max(4, h.x + 10));
            const by = Math.min(layout.contentHeight - boxH - 4, h.y + 12);
            ctx.fillStyle = "rgba(14,17,22,0.94)";
            ctx.strokeStyle = GRID_MAJOR;
            ctx.fillRect(bx, by, boxW, boxH);
            ctx.strokeRect(bx, by, boxW, boxH);
            ctx.fillStyle = INK;
            ctx.font = "9px ui-monospace, monospace";
            lines.forEach((ln, i) => ctx.fillText(ln, bx + 5, by + 13 + i * 12));
          }
        }
      }
    }

    // ---- brush minimap ----
    const brush = brushRef.current;
    if (brush) {
      brush.width = Math.floor(width * dpr);
      brush.height = Math.floor(BRUSH_H * dpr);
      brush.style.width = `${width}px`;
      brush.style.height = `${BRUSH_H}px`;
      const ctx = brush.getContext("2d");
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, BRUSH_H);
        const px = (t: number) => ((t - bounds.min) / Math.max(1, bounds.max - bounds.min)) * width;
        // density histogram of ALL fetched granule times
        const buckets = Math.max(24, Math.floor(width / 6));
        const counts = new Array<number>(buckets).fill(0);
        for (const b of barsByStart) {
          const f = (b.start - bounds.min) / Math.max(1, bounds.max - bounds.min);
          const idx = Math.min(buckets - 1, Math.max(0, Math.floor(f * buckets)));
          counts[idx] += 1;
        }
        const maxC = Math.max(1, ...counts);
        ctx.fillStyle = "#2c5f58";
        for (let i = 0; i < buckets; i++) {
          const h = (counts[i] / maxC) * (BRUSH_H - 18);
          if (h <= 0) continue;
          ctx.fillRect((i / buckets) * width, BRUSH_H - 6 - h, width / buckets - 1, h);
        }
        // viewport window
        const x0 = px(scaleRef.current.t0);
        const x1 = px(scaleRef.current.t1);
        ctx.fillStyle = "rgba(63,182,201,0.12)";
        ctx.fillRect(x0, 0, Math.max(3, x1 - x0), BRUSH_H);
        ctx.strokeStyle = CYAN;
        ctx.strokeRect(x0 + 0.5, 0.5, Math.max(3, x1 - x0) - 1, BRUSH_H - 1);
        ctx.fillStyle = MUTED;
        ctx.font = "9px ui-monospace, monospace";
        ctx.textAlign = "left";
        ctx.fillText(fmtSpan(bounds.min), 4, 9);
        ctx.textAlign = "right";
        ctx.fillText(fmtSpan(bounds.max), width - 4, 9);
      }
    }
  }, [barsByStart, bounds, width]);

  // ---- interaction: pan / zoom / hover / click / dblclick / keyboard ----------------------------
  const END_PX = 8;                                     // pair end-dot tolerance (§4.4)
  const hitTest = useCallback((x: number, y: number): PackedHit | null => {
    const s = scaleRef.current;
    const layout = stackLayoutRef.current;
    const t = xToT({ ...s, widthPx: width }, x);
    for (const group of layout.groups) {
      if (y < group.yTop + STACK_HEADER_H || y >= group.yTop + group.height) continue;
      const lane = Math.floor((y - group.yTop - STACK_HEADER_H) / LANE_H);
      const row = group.rows[lane];
      if (!row) continue;
      // interval index: row is sorted by start; scan (rows are small per lane)
      for (const bar of row) {
        const x1 = tToX({ ...s, widthPx: width }, bar.start);
        const x2 = Math.max(tToX({ ...s, widthPx: width }, bar.end), x1 + 3);
        if (x >= x1 - 3 && x <= x2 + 3) {
          const isPair = bar.kind === "gunw" || bar.kind === "goff";
          let end: "ref" | "sec" | null = null;
          if (isPair && Math.abs(x - x1) <= END_PX) end = "ref";
          else if (isPair && Math.abs(x - x2) <= END_PX) end = "sec";
          return { bar, end };
        }
      }
      void t;
    }
    return null;
  }, [width]);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    dragRef.current = { x: e.clientX, t0: scaleRef.current.t0, t1: scaleRef.current.t1 };
    (e.target as Element).setPointerCapture(e.pointerId);
  }, []);
  const onPointerMove = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top + (e.currentTarget.closest(".lanes-scroll")?.scrollTop ?? 0) * 0;
    const t = xToT({ ...scaleRef.current, widthPx: width }, x);
    if (dragRef.current) {
      const dx = e.clientX - dragRef.current.x;
      setScale(panScale({ t0: dragRef.current.t0, t1: dragRef.current.t1, widthPx: width }, -dx, bounds));
      setHover(null);
      return;
    }
    const hit = hitTest(x, y);
    setHover({ x, y, t, bar: hit?.bar ?? null, end: hit?.end ?? null });
  }, [bounds, width, hitTest]);
  const onPointerUp = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    const moved = Math.abs(e.clientX - drag.x) > 4;
    if (!moved) {
      const rect = e.currentTarget.getBoundingClientRect();
      const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
      if (hit) {
        const { bar, end } = hit;
        // §4.4: a click on a pair END opens the Compare view for that date
        if (end && bar.group && propsRef.current.onEndClick) {
          propsRef.current.onEndClick(bar.group.key, end);
          return;
        }
        propsRef.current.onSelect(bar.id, e.shiftKey || e.ctrlKey || e.metaKey);
        propsRef.current.onFocus(bar.id);
      } else if (!e.shiftKey && !e.ctrlKey && !e.metaKey) {
        propsRef.current.onFocus(null);
      }
    }
  }, [hitTest]);
  const onDoubleClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
    if (!hit) return;
    const bar = hit.bar;
    const span = Math.max(bar.end - bar.start, DAY_MS / 24) * 2.4;
    const center = (bar.start + bar.end) / 2;
    setScale(clamp({ t0: center - span / 2, t1: center + span / 2, widthPx: width }, bounds));
  }, [bounds, hitTest, width]);

  // wheel zoom (non-passive listener so preventDefault sticks)
  useEffect(() => {
    const el = lanesRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const x = e.clientX - rect.left;
      setScale((s) => zoomScale({ ...s, widthPx: width }, e.deltaY < 0 ? 1.18 : 1 / 1.18, x, bounds));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [bounds, width]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLCanvasElement>) => {
    const s = scaleRef.current;
    const p = propsRef.current;
    if (e.key === "ArrowLeft") {
      setScale(panScale(s, width * 0.15, bounds));
    } else if (e.key === "ArrowRight") {
      setScale(panScale(s, -width * 0.15, bounds));
    } else if (e.key === "+" || e.key === "=") {
      setScale(zoomScale({ ...s, widthPx: width }, 1.3, width / 2, bounds));
    } else if (e.key === "-" || e.key === "_") {
      setScale(zoomScale({ ...s, widthPx: width }, 1 / 1.3, width / 2, bounds));
    } else if (e.key === "Home") {
      setScale(clamp({ ...s, t0: bounds.min, t1: bounds.max, widthPx: width }, bounds));
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      // roving focus across bars (sorted by start; deterministic order)
      const idx = barsByStart.findIndex((b) => b.id === p.focusedId);
      const next = e.key === "ArrowDown"
        ? barsByStart[Math.min(barsByStart.length - 1, idx + 1)]
        : barsByStart[Math.max(0, idx <= 0 ? 0 : idx - 1)];
      if (next) {
        p.onFocus(next.id);
        if (!p.selectedIds.includes(next.id)) p.onSelect(next.id, e.shiftKey);
      }
    } else if (e.key === "Enter") {
      if (p.focusedId) p.onSelect(p.focusedId, e.shiftKey);
    } else {
      return;
    }
    e.preventDefault();
  }, [barsByStart, bounds, width]);

  // ---- brush interactions -----------------------------------------------------------------------
  const brushPan = useCallback((clientX: number, drag: { x: number; t0: number; t1: number }) => {
    const frac = (clientX - drag.x) / Math.max(1, width);
    const span = drag.t1 - drag.t0;
    const total = bounds.max - bounds.min;
    const shift = frac * total;
    let t0 = drag.t0 - shift;
    t0 = Math.max(bounds.min, Math.min(bounds.max - span, t0));
    setScale({ t0, t1: t0 + span, widthPx: width });
  }, [bounds, width]);

  return (
    <div ref={wrapRef} className="flex min-h-0 flex-1 flex-col rounded-lg border border-[#262c36] bg-[#0e1116]">
      <canvas ref={axisRef} aria-hidden className="block" />
      <div className="lanes-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        <canvas
          ref={lanesRef}
          role="application"
          tabIndex={0}
          aria-label={`granule timeline, UTC; arrow keys pan, plus and minus zoom, up and down arrows move between granules, Enter selects; ${flatBars.length} granules in ${props.stacks.length} stacks`}
          className="block cursor-grab touch-none select-none focus:outline-none focus-visible:ring-1 focus-visible:ring-[#3fb6c9]"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={() => setHover(null)}
          onDoubleClick={onDoubleClick}
          onKeyDown={onKeyDown}
        />
      </div>
      <canvas
        ref={brushRef}
        aria-label="timeline brush minimap: drag to pan over the full searched window"
        role="slider"
        tabIndex={0}
        aria-valuemin={bounds.min}
        aria-valuemax={bounds.max}
        aria-valuenow={Math.round((scale.t0 + scale.t1) / 2)}
        className="block cursor-grab touch-none select-none border-t border-[#262c36]"
        onPointerDown={(e) => {
          brushDragRef.current = { x: e.clientX, t0: scaleRef.current.t0, t1: scaleRef.current.t1 };
          (e.target as Element).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (brushDragRef.current) brushPan(e.clientX, brushDragRef.current);
        }}
        onPointerUp={() => {
          brushDragRef.current = null;
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") setScale(panScale(scaleRef.current, width * 0.15, bounds));
          else if (e.key === "ArrowRight") setScale(panScale(scaleRef.current, -width * 0.15, bounds));
          else return;
          e.preventDefault();
        }}
      />
      <p className="border-t border-[#262c36] px-2 py-1 text-[10px] text-[#5a636e]">
        bar = pair: two dates, one dot per end (click a dot to compare that date) · dot = single
        date · hollow diamond = unknown layout (raw) · ×N = engine-grouped variants · drag to pan ·
        wheel to zoom at the cursor · double-click a bar to zoom to it · ←/→ pan · +/− zoom ·
        ↑/↓ move between granules · Home fits the searched window · all times UTC
        {" · "}{flatBars.length} bars
      </p>
    </div>
  );
}
