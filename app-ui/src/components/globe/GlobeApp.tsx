"use client";

/** GlobeApp (§7) — the globe landing experience. Orchestrates: engine health + cache lists, the
 *  pin -> tile-at flow (debounced, AbortController), scans (POST /api/scans, pollWhile), results
 *  (live slim run + pipeline-scoped layers, or cached rows), overlay rendering with an LRU layer
 *  cache, the Data tab, and URL state (§7.7). Honesty rules: engine strings verbatim, an empty
 *  cache renders an honest empty state, a field the engine doesn't compute is simply absent. */
import { initialCamera } from "@/lib/camera";
import { boxFromCenter } from "@/lib/boxFromCenter";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { X } from "lucide-react";
import { api, engineBase, pollWhile } from "@/lib/api";
import { useEngineUrl } from "@/lib/engineUrl";
import { cache, cacheConfigured } from "@/lib/cache";
import { colorize, validateLayerPayload, decodeLayer } from "@/lib/raster";
import { parseCoordinates } from "@/lib/geo-input";
import { mostNotableTier, TIER_PIN_COLORS } from "@/lib/status";
import { parseUrlState, writeUrlState, type PanelMode, type UrlState } from "@/lib/urlstate";
import { clearQueryContext, saveQueryContext } from "@/lib/queryContext";
import type {
  CachedSummary, CachedVerdict, FeaturedArea, Health, LayerPayload, PipelineInfo, RunState, TileInfo,
} from "@/lib/types";
import { timeAgo } from "@/lib/format";
import SearchBar from "./SearchBar";
import AppNav from "./AppNav";
import Legend from "./Legend";
import AreasPanel from "./AreasPanel";
import ScanPanel from "./ScanPanel";
import FindingsPanel, { type LayerSource } from "./FindingsPanel";
import DataPanel from "./DataPanel";
import BottomSheet from "./BottomSheet";
import type { OverlaySpec } from "./GlobeMap";

const GlobeMap = dynamic(() => import("./GlobeMap"), {
  ssr: false,
  loading: () => <div className="flex h-full w-full items-center justify-center bg-[#0b0d12]"><p className="text-xs text-[#8a837b]">loading the globe…</p></div>,
});

const DEFAULT_LOOKBACK_DAYS = 120; // the engine's own default window (U8: common.yaml window.lookback_days)

interface LayerCacheEntry {
  dataUrl: string;
  bounds: [number, number, number, number];
  payload: LayerPayload;
}

export default function GlobeApp() {
  const engineUrl = useEngineUrl();
  const router = useRouter();
  const searchParams = useSearchParams();

  // ---- engine + cache state ---------------------------------------------------------------
  const [health, setHealth] = useState<Health | null>(null);
  const [healthChecked, setHealthChecked] = useState(false);
  const [pipelines, setPipelines] = useState<PipelineInfo[] | null>(null);
  const [featured, setFeatured] = useState<FeaturedArea[] | null>(null);
  const [cachedRows, setCachedRows] = useState<CachedSummary[] | null>(null);
  const [cachedTotal, setCachedTotal] = useState<number | null>(null);
  const [cacheError, setCacheError] = useState<string | null>(null);

  // ---- selection state ---------------------------------------------------------------------
  const [pin, setPin] = useState<[number, number] | null>(null);
  const [latInput, setLatInput] = useState("");
  const [lonInput, setLonInput] = useState("");
  const [tile, setTile] = useState<TileInfo | null>(null);
  const [tileError, setTileError] = useState<string | null>(null);
  const [tileLoading, setTileLoading] = useState(false);
  // Range (km): a square box of this side around the pin. Empty = the engine's own tile box.
  const [rangeInput, setRangeInput] = useState("");
  const customBox = useMemo(() => {
    if (!pin || rangeInput.trim() === "") return null;
    return boxFromCenter(pin[0], pin[1], Number(rangeInput));
  }, [pin, rangeInput]);
  const rangeBad = rangeInput.trim() !== "" && !customBox;
  const shownPolygon = useMemo<[number, number][] | null>(() => (customBox
    ? [[customBox[0], customBox[1]], [customBox[2], customBox[1]], [customBox[2], customBox[3]],
       [customBox[0], customBox[3]], [customBox[0], customBox[1]]]
    : (tile?.polygon_lonlat ?? null)), [customBox, tile?.polygon_lonlat]);
  const [panel, setPanel] = useState<PanelMode>("explore");
  const [selectedTile, setSelectedTile] = useState<string | null>(null);
  const [cachedForTile, setCachedForTile] = useState<CachedSummary[] | null>(null);

  // ---- run + results state ------------------------------------------------------------------
  const [scanRunId, setScanRunId] = useState<string | null>(null);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [busyRunId, setBusyRunId] = useState<string | null>(null);
  const [resultsBundleId, setResultsBundleId] = useState<string | null>(null); // cached source
  const [resultsRunId, setResultsRunId] = useState<string | null>(null);       // live source
  const [cachedResults, setCachedResults] = useState<{ rows: CachedVerdict[]; bundleId: string } | null>(null);
  const [selectedPipeline, setSelectedPipeline] = useState<string | null>(null);
  const [selectedDetection, setSelectedDetection] = useState<{ id: string; bbox: [number, number, number, number] } | null>(null);

  // ---- overlays ------------------------------------------------------------------------------
  const [overlays, setOverlays] = useState<Record<string, { pipeline: string; layer: string; opacity: number }>>({});
  const [overlayErrors, setOverlayErrors] = useState<Record<string, string>>({});
  const layerCache = useRef<Map<string, LayerCacheEntry>>(new Map());
  const lastOverlayOrder = useRef<string[]>([]);

  // ---- map state -----------------------------------------------------------------------------
  // the map is CREATED at the link's camera (or the last saved one) — no world view followed by a fly
  const [initialCam] = useState(() => initialCamera(new URLSearchParams(searchParams.toString())));
  const [flat, setFlat] = useState(false);
  const [flyTarget, setFlyTarget] = useState<{ center: [number, number]; zoom: number; key: number } | null>(null);
  const [pairFootprint, setPairFootprint] = useState<[number, number][] | null>(null);
  const [webglMsg, setWebglMsg] = useState<string | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [isNarrow, setIsNarrow] = useState(false);
  const zoomRef = useRef<number | undefined>(undefined);
  const pinRef = useRef<[number, number] | null>(null);
  useEffect(() => { pinRef.current = pin; }, [pin]);

  const engineOnline = healthChecked ? health !== null : false;

  // ---- boot: URL state + env -----------------------------------------------------------------
  useEffect(() => {
    const st: UrlState = parseUrlState(new URLSearchParams(searchParams.toString()));
    if (st.lat !== undefined && st.lon !== undefined) {
      setPin([st.lat, st.lon]);
      setLatInput(String(st.lat));
      setLonInput(String(st.lon));
    }
    if (st.tile && (st.lat === undefined || st.lon === undefined)) {
      // §7.7: a pasted ?tile= (or ?panel=granules&tile=) link restores the same view — the
      // engine resolves the id back to geometry; the UI never inverts MGRS ids itself.
      api.tileAtId(st.tile).then((r) => {
        const [clon, clat] = r.tile.center_lonlat;
        setSelectedTile(r.tile.id);
        setPin([clat, clon]);
        setLatInput(String(Number(clat.toFixed(5))));
        setLonInput(String(Number(clon.toFixed(5))));
        setFlyTarget({ center: [clon, clat], zoom: st.z ?? 11.5, key: Date.now() });
      }).catch(() => {
        // unknown tile id: stay on the default view, nothing invented
      });
    }
    if (st.z !== undefined) zoomRef.current = st.z;
    if (st.tile) setSelectedTile(st.tile);
    if (st.scan) {
      setScanRunId(st.scan);
      setPanel("results");
    } else if (st.bundle) {
      setResultsBundleId(st.bundle);
      setPanel("results");
    }
    if (st.panel !== "explore") setPanel(st.panel);
    try {
      const saved = window.localStorage.getItem("globe-flat");
      if (saved === "1") setFlat(true);
    } catch { /* private mode */ }
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const onMq = () => setReducedMotion(mq.matches);
    mq.addEventListener?.("change", onMq);
    const onResize = () => setIsNarrow(window.innerWidth < 1024);
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      mq.removeEventListener?.("change", onMq);
      window.removeEventListener("resize", onResize);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- engine + cache loads ------------------------------------------------------------------
  const refreshLists = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null)).finally(() => setHealthChecked(true));
    api.pipelines().then((r) => setPipelines(r.pipelines)).catch(() => setPipelines(null));
    const cacheErrorText = (e: unknown) => {
      const message = e instanceof Error ? e.message : String(e);
      // a bare network refusal ("Failed to fetch") names nothing — say what failed
      return /failed to fetch|networkerror|load failed/i.test(message)
        ? `the verdict cache is unreachable (${message})`
        : message;
    };
    if (cacheConfigured) {
      cache
        .pins(200)
        .then(({ rows, total }) => {
          setCachedRows(rows);
          setCachedTotal(total);
          setCacheError(null);
        })
        .catch((e) => setCacheError(cacheErrorText(e)));
      cache
        .featured()
        .then(setFeatured)
        .catch((e) => setCacheError(cacheErrorText(e)));
    } else {
      setFeatured([]);
      setCachedRows([]);
    }
  }, []);

  useEffect(() => {
    refreshLists();
    const id = setInterval(refreshLists, 30_000);
    return () => clearInterval(id);
  }, [refreshLists]);

  // ---- URL state write-back (debounced; not during map motion) -------------------------------
  const urlTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pushUrl = useCallback((patch: Partial<UrlState>) => {
    if (urlTimer.current) clearTimeout(urlTimer.current);
    urlTimer.current = setTimeout(() => {
      const st = parseUrlState(new URLSearchParams(window.location.search));
      const next = { ...st, ...patch };
      const params = new URLSearchParams(window.location.search);
      writeUrlState(params, next);
      router.replace(`/map?${params.toString()}`, { scroll: false });
    }, 350);
  }, [router]);

  // ---- pin -> tile-at (debounced ~300ms, AbortController, out-of-order ignored) ---------------
  useEffect(() => {
    if (!pin) {
      setTile(null);
      setTileError(null);
      return;
    }
    // remember the pin NOW (before the tile resolves) so Background can pick it up even if the
    // user switches tab mid-load; the previous pin's tile/box is dropped so it can't leak across
    saveQueryContext({ lat: pin[0], lon: pin[1], tile: undefined, bbox: undefined });
    const ctrl = new AbortController();
    setTileLoading(true);
    const id = setTimeout(() => {
      fetch(`${engineBase()}/api/tile-at?lon=${pin[1]}&lat=${pin[0]}`, { signal: ctrl.signal, cache: "no-store" })
        .then(async (res) => {
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body?.detail?.error ?? body?.detail ?? `${res.status} ${res.statusText}`);
          return body as { ok: true; tile: TileInfo };
        })
        .then((body) => {
          setTile(body.tile);
          setTileError(null);
          setSelectedTile(body.tile.id);
          // the engine's own box for this tile — exactly the area the Data tab searches
          saveQueryContext({
            lat: pin[0], lon: pin[1], tile: body.tile.id, bbox: body.tile.bbox_lonlat,
            level: "GUNW,GCOV",
          });
          pushUrl({ tile: body.tile.id, lat: pin[0], lon: pin[1] });
        })
        .catch((e) => {
          if (ctrl.signal.aborted) return;
          setTile(null);
          setTileError(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setTileLoading(false);
        });
    }, 300);
    return () => {
      ctrl.abort();
      clearTimeout(id);
    };
  }, [pin, pushUrl]);

  // the Data retrieval tab searches exactly the box drawn here: a custom range replaces the tile box
  useEffect(() => {
    if (!pin) return;
    if (customBox) saveQueryContext({ lat: pin[0], lon: pin[1], bbox: customBox });
    else if (tile?.bbox_lonlat) saveQueryContext({ lat: pin[0], lon: pin[1], bbox: tile.bbox_lonlat });
  }, [customBox, tile?.bbox_lonlat, pin]);

  // ---- cached rows for the selected tile -------------------------------------------------------
  useEffect(() => {
    if (!tile?.id || !cacheConfigured) {
      setCachedForTile(null);
      return;
    }
    let alive = true;
    cache
      .forTile(tile.id)
      .then((rows) => alive && setCachedForTile(rows))
      .catch(() => alive && setCachedForTile([]));
    return () => {
      alive = false;
    };
  }, [tile?.id]);

  // ---- poll a running scan / re-attach ---------------------------------------------------------
  useEffect(() => {
    if (!scanRunId) return;
    const stop = pollWhile(
      () =>
        api
          .run(scanRunId, { slim: true })
          .then((state) => ({ done: state.status === "ok" || state.status === "error", value: state })),
      (state: RunState) => {
        setRunState(state);
        if (state.status === "ok") {
          setResultsRunId(scanRunId);
          setResultsBundleId(null);
          setPanel("results");
          pushUrl({ scan: scanRunId, panel: "results" });
          refreshLists();
          // re-attach (?scan= without coordinates): re-centre on the scanned tile so the
          // overlays are actually visible — the engine resolves the id back to a centre
          if (!pinRef.current && state.tile) {
            api.tileAtId(state.tile).then((r) => {
              const [clon, clat] = r.tile.center_lonlat;
              setSelectedTile(r.tile.id);
              setPin([clat, clon]);
              setLatInput(String(Number(clat.toFixed(5))));
              setLonInput(String(Number(clon.toFixed(5))));
              setFlyTarget({ center: [clon, clat], zoom: 11.5, key: Date.now() });
            }).catch(() => undefined);
          }
        }
      },
      1500,
      (message) => setRunError(message),
    );
    return stop;
  }, [scanRunId, pushUrl, refreshLists]);

  // ---- cached bundle results -------------------------------------------------------------------
  useEffect(() => {
    if (!resultsBundleId || !cacheConfigured) {
      if (!resultsRunId) setCachedResults(null);
      return;
    }
    let alive = true;
    cache
      .forBundle(resultsBundleId)
      .then((rows: CachedVerdict[]) => {
        if (!alive) return;
        setCachedResults({ rows, bundleId: resultsBundleId });
        setResultsRunId(null);
        setRunState(null);
      })
      .catch((e) => alive && setRunError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [resultsBundleId, cacheConfigured, resultsRunId]);

  // ---- keyboard: Esc clears the selection / closes the panel -----------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPanel("explore");
        setPin(null);
        setSelectedTile(null);
        setSelectedDetection(null);
        setPairFootprint(null);
        clearQueryContext();
        pushUrl({ panel: undefined, tile: undefined, scan: undefined, bundle: undefined, lat: undefined, lon: undefined });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pushUrl]);

  // ---- overlay loading (LRU 12, one colour implementation) --------------------------------------
  const loadLayerPayload = useCallback(async (source: "live" | "cached", sourceId: string, pipeline: string, layer: string): Promise<LayerPayload> => {
    const key = `${source}|${sourceId}|${pipeline}|${layer}`;
    const hit = layerCache.current.get(key);
    if (hit) return hit.payload;
    const payload =
      source === "live"
        ? (await api.pipelineLayer(sourceId, pipeline, layer)).layer
        : await cache.layer(sourceId, pipeline, layer);
    if (layerCache.current.size >= 12) {
      const oldest = layerCache.current.keys().next().value;
      if (oldest) layerCache.current.delete(oldest);
    }
    return payload;
  }, []);

  const layerSource: LayerSource = useCallback(
    (pipeline: string, layer: string) => {
      if (resultsRunId) return loadLayerPayload("live", resultsRunId, pipeline, layer);
      if (cachedResults) return loadLayerPayload("cached", cachedResults.bundleId, pipeline, layer);
      return Promise.reject(new Error("no result is open"));
    },
    [resultsRunId, cachedResults, loadLayerPayload],
  );

  const ensureOverlay = useCallback(
    async (pipeline: string, layer: string) => {
      const key = `${pipeline}:${layer}`;
      if (overlays[key]) return;
      try {
        const sourceId = resultsRunId ?? cachedResults?.bundleId;
        if (!sourceId) throw new Error("no result is open");
        const source = resultsRunId ? "live" : "cached";
        const entryKey = `${source}|${sourceId}|${pipeline}|${layer}`;
        let entry = layerCache.current.get(entryKey);
        if (!entry) {
          const payload = await loadLayerPayload(source, sourceId, pipeline, layer);
          const problems = validateLayerPayload(payload);
          if (problems.length) {
            throw new Error(`layer present but not displayable: ${problems.join("; ")}`);
          }
          const decoded = decodeLayer(payload);
          if (!decoded.ok) throw new Error(`layer present but not displayable: ${decoded.reason}`);
          const canvas = document.createElement("canvas");
          colorize(payload, decoded.data, canvas);
          entry = {
            dataUrl: canvas.toDataURL(),
            bounds: payload.bounds_lonlat as [number, number, number, number],
            payload,
          };
          if (layerCache.current.size >= 12) {
            const oldest = layerCache.current.keys().next().value;
            if (oldest) layerCache.current.delete(oldest);
          }
          layerCache.current.set(entryKey, entry);
        }
        setOverlayErrors((m) => {
          const { [key]: _drop, ...rest } = m;
          return rest;
        });
        lastOverlayOrder.current = [...lastOverlayOrder.current.filter((k) => k !== key), key];
        setOverlays((m) => ({
          ...m,
          [key]: { pipeline, layer, opacity: m[key]?.opacity ?? 0.8 },
        }));
      } catch (e) {
        setOverlayErrors((m) => ({ ...m, [key]: e instanceof Error ? e.message : String(e) }));
      }
    },
    [overlays, resultsRunId, cachedResults, loadLayerPayload],
  );

  // NOTE: data: URLs need no revoking; the LRU cap (12) bounds memory.

  const toggleOverlay = useCallback(
    (pipeline: string, layer: string, kind: string, on: boolean) => {
      const key = `${pipeline}:${layer}`;
      setOverlays((m) => {
        const next = { ...m };
        if (on && kind === "raster") {
          void ensureOverlay(pipeline, layer);
          lastOverlayOrder.current = [...lastOverlayOrder.current.filter((k) => k !== key), key];
          next[key] = { pipeline, layer, opacity: m[key]?.opacity ?? 0.8 };
        } else {
          delete next[key];
          setPairFootprint(null);
        }
        return next;
      });
    },
    [ensureOverlay],
  );

  const setOverlayOpacity = useCallback((pipeline: string, layer: string, opacity: number) => {
    setOverlays((m) => ({ ...m, [`${pipeline}:${layer}`]: { pipeline, layer, opacity } }));
  }, []);

  // ---- map specs -------------------------------------------------------------------------------
  const overlaySpecs: OverlaySpec[] = useMemo(() => {
    const specs: OverlaySpec[] = [];
    for (const key of lastOverlayOrder.current) {
      const o = overlays[key];
      if (!o) continue;
      const sourceId = resultsRunId ?? cachedResults?.bundleId;
      if (!sourceId) continue;
      const entry = layerCache.current.get(`${resultsRunId ? "live" : "cached"}|${sourceId}|${o.pipeline}|${o.layer}`);
      if (entry) specs.push({ id: `overlay:${key}`, dataUrl: entry.dataUrl, bounds: entry.bounds, opacity: o.opacity });
    }
    return specs;
  }, [overlays, resultsRunId, cachedResults]);

  const pins: { tile: string; lon: number; lat: number; tier: string; label: string }[] = useMemo(() => {
    const out: { tile: string; lon: number; lat: number; tier: string; label: string }[] = [];
    if (cachedRows) {
      const best = new Map<string, CachedSummary>();
      for (const r of cachedRows) {
        if (r.center_lon == null || r.center_lat == null) continue;
        const cur = best.get(r.tile);
        if (!cur || cur.computed_at < r.computed_at) best.set(r.tile, r);
      }
      for (const [t, r] of best) {
        out.push({
          tile: t, lon: r.center_lon as number, lat: r.center_lat as number,
          tier: TIER_PIN_COLORS[r.tier ?? "insufficient"] ? (r.tier as string) : "insufficient",
          label: `${t} · ${r.status.replace(/_/g, " ").toLowerCase()}`,
        });
      }
    }
    return out;
  }, [cachedRows]);

  // ---- actions ----------------------------------------------------------------------------------
  const selectPoint = useCallback((lat: number, lon: number, opts?: { zoom?: number; panel?: PanelMode }) => {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    setPin([lat, lon]);
    setLatInput(String(Number(lat.toFixed(5))));
    setLonInput(String(Number(lon.toFixed(5))));
    setSelectedDetection(null);
    setPairFootprint(null);
    setFlyTarget({ center: [lon, lat], zoom: opts?.zoom ?? Math.max(zoomRef.current ?? 0, 10.5), key: Date.now() });
    if (opts?.panel) setPanel(opts.panel);
    pushUrl({ lat, lon, panel: opts?.panel });
  }, [pushUrl]);

  const onMapClick = useCallback(
    (lat: number, lon: number) => {
      selectPoint(lat, lon, { panel: "pin" });
    },
    [selectPoint],
  );

  const startScan = useCallback(
    (lon: number, lat: number) => {
      setRunError(null);
      setBusyRunId(null);
      api
        .startScan(lon, lat, { lookback_days: DEFAULT_LOOKBACK_DAYS })
        .then((r) => {
          setScanRunId(r.run_id);
          setPanel("pin");
          pushUrl({ scan: r.run_id, panel: "pin" });
        })
        .catch((e) => {
          const message = e instanceof Error ? e.message : String(e);
          setRunError(message);
          const m = /run-[0-9a-f]{12}/.exec(message);
          if (m) setBusyRunId(m[0]);
        });
    },
    [pushUrl],
  );

  const openCachedResult = useCallback(
    (bundleId: string) => {
      setResultsBundleId(bundleId);
      setResultsRunId(null);
      setScanRunId(null);
      setRunState(null);
      setPanel("results");
      pushUrl({ bundle: bundleId, panel: "results" });
    },
    [pushUrl],
  );

  const openRunResult = useCallback(
    (runId: string) => {
      setScanRunId(runId);
      setResultsBundleId(null);
      setResultsRunId(null);
      setPanel("results");
      pushUrl({ scan: runId, panel: "results" });
    },
    [pushUrl],
  );

  // once a live run finished, fetch its full (still slim) state for the findings list
  useEffect(() => {
    if (!resultsRunId) return;
    let alive = true;
    api
      .run(resultsRunId, { slim: true })
      .then((state) => alive && setRunState(state))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [resultsRunId]);

  // attach a scan id from the URL without re-running
  useEffect(() => {
    const st = parseUrlState(new URLSearchParams(searchParams.toString()));
    if (st.scan && st.scan !== scanRunId && !runState) {
      setScanRunId(st.scan);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const results: RunState | null = runState;
  const implementedCount = pipelines?.filter((p) => p.implemented).length ?? null;
  const totalCount = pipelines?.length ?? null;

  const activeOverlayLegend = useMemo(() => {
    const keys = Object.keys(overlays);
    if (keys.length === 0) return null;
    const key = lastOverlayOrder.current[lastOverlayOrder.current.length - 1] ?? keys[keys.length - 1];
    const o = overlays[key];
    if (!o) return null;
    const sourceId = resultsRunId ?? cachedResults?.bundleId;
    if (!sourceId) return null;
    const entry = layerCache.current.get(`${resultsRunId ? "live" : "cached"}|${sourceId}|${o.pipeline}|${o.layer}`);
    return entry ? { layer: entry.payload, opacity: o.opacity } : null;
  }, [overlays, resultsRunId, cachedResults]);

  const panelContent = (
    <>
      <div className="flex items-center justify-between border-b border-[#f0ece4] px-3 py-2">
        <div role="tablist" aria-label="Panel sections" className="flex gap-1">
          {([
            ["explore", "Explore"],
            ["pin", "Pin"],
            ["results", "Results"],
            ["granules", "Data"],
          ] as const).map(([mode, label]) => (
            <button
              key={mode}
              role="tab"
              aria-selected={panel === mode}
              onClick={() => {
                setPanel(mode);
                pushUrl({ panel: mode });
              }}
              className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                panel === mode ? "bg-[#1c1a17] text-[#faf9f6]" : "text-[#4a443c] hover:bg-[#f0ece4]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          onClick={() => {
            setPanel("explore");
            setPin(null);
            setSelectedDetection(null);
            setPairFootprint(null);
            clearQueryContext();
            pushUrl({ panel: undefined, tile: undefined, scan: undefined, bundle: undefined, lat: undefined, lon: undefined });
          }}
          className="rounded p-1 text-[#8a837b] hover:bg-[#f0ece4]"
          title="clear selection (Esc)"
          aria-label="clear selection"
        >
          <X size={14} aria-hidden />
        </button>
      </div>

      {!healthChecked && <div className="m-3 skeleton h-10 rounded-lg" aria-busy="true" />}
      {healthChecked && (
        <p role="status" className="mx-3 mt-2 rounded-lg border border-[#e7e2da] bg-white px-3 py-2 text-[11px] text-[#4a443c]">
          {engineOnline ? (
            <>
              engine online
              {health?.scans_enabled ? " · scans enabled" : " · scans disabled (set NISAR_ENABLE_SCANS=1)"}
              {health?.earthdata_credentials ? ` · Earthdata ${health.earthdata_credentials}` : ""}
              {health?.cache_write ? ` · cache ${health.cache_write.status}` : ""}
            </>
          ) : (
            <>
              engine offline at <span className="mono">{engineUrl}</span> — cached results still open;
              scans and the Data tab need the engine.
            </>
          )}
        </p>
      )}

      {panel === "explore" && (
        <AreasPanel
          featured={featured}
          cachedRows={cachedRows}
          cachedTotal={cachedTotal}
          cacheError={cacheError}
          cacheConfigured={cacheConfigured}
          selectedTile={selectedTile}
          onSelectTile={(t, lon, lat, zoom) => {
            setSelectedTile(t);
            if (Number.isFinite(lon) && Number.isFinite(lat)) {
              selectPoint(lat, lon, { zoom: zoom ?? 11.5, panel: "pin" });
            }
          }}
          onOpenResult={openCachedResult}
          onScan={(t, lon, lat) => {
            if (Number.isFinite(lon) && Number.isFinite(lat)) {
              selectPoint(lat, lon, { zoom: 11.5, panel: "pin" });
            }
          }}
        />
      )}
      {panel === "pin" && (
        <ScanPanel
          tile={tile}
          tileError={tileError}
          tileLoading={tileLoading}
          pin={pin}
          cachedForTile={cachedForTile}
          health={health}
          engineOnline={engineOnline}
          implementedCount={implementedCount}
          totalCount={totalCount}
          runState={runState}
          runError={runError}
          busyRunId={busyRunId}
          onScan={() => pin && startScan(pin[1], pin[0])}
          onOpenResult={(id) => (scanRunId && id === scanRunId ? openRunResult(id) : openCachedResult(id))}
          onOpenDataTab={() => {
            setPanel("granules");
            pushUrl({ panel: "granules" });
          }}
        />
      )}
      {panel === "results" && (() => {
        const overlayErrorEntries = Object.entries(overlayErrors);
        if (resultsRunId && results) {
          return (
            <>
              {overlayErrorEntries.length > 0 && (
                <p role="status" className="mx-3 mt-2 rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[11px] text-[#7a6438]">
                  {overlayErrorEntries.map(([k, v]) => (
                    <span key={k} className="block">{v}</span>
                  ))}
                </p>
              )}
              <FindingsPanel
                results={results.results}
                title={`Scan ${results.id}`}
                sourceBadge="live run"
                scanTimeIso={null}
                tile={results.tile ?? tile?.id ?? null}
                bundleId={results.bundle_id ?? null}
                runId={results.id}
                loadLayer={layerSource}
                cacheReport={results.cache ?? null}
                selectedPipeline={selectedPipeline}
                onSelectPipeline={setSelectedPipeline}
                activeOverlays={overlays}
                onToggleOverlay={toggleOverlay}
                onOverlayOpacity={setOverlayOpacity}
                onSelectDetection={(d) => {
                  setSelectedDetection(d);
                  setFlyTarget({ center: [(d.bbox[1] + d.bbox[3]) / 2, (d.bbox[0] + d.bbox[2]) / 2], zoom: 12.5, key: Date.now() });
                }}
                selectedDetectionId={selectedDetection?.id ?? null}
                linkFullReport={`/runs/${results.id}`}
              />
            </>
          );
        }
        if (cachedResults) {
          const primaryRows = cachedResults.rows;
          const resultsList = primaryRows.map((r) => r.result);
          return (
            <>
              {overlayErrorEntries.length > 0 && (
                <p role="status" className="mx-3 mt-2 rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[11px] text-[#7a6438]">
                  {overlayErrorEntries.map(([k, v]) => (
                    <span key={k} className="block">{v}</span>
                  ))}
                </p>
              )}
              <FindingsPanel
                results={resultsList}
                title={`Cached verdict · tile ${primaryRows[0]?.tile ?? "?"}`}
                sourceBadge={`cached · ${timeAgo(primaryRows[0]?.computed_at)}`}
                scanTimeIso={primaryRows[0]?.computed_at ?? null}
                tile={primaryRows[0]?.tile ?? null}
                bundleId={cachedResults.bundleId}
                runId={primaryRows[0]?.run_id ?? null}
                loadLayer={layerSource}
                selectedPipeline={selectedPipeline}
                onSelectPipeline={setSelectedPipeline}
                activeOverlays={overlays}
                onToggleOverlay={toggleOverlay}
                onOverlayOpacity={setOverlayOpacity}
                onSelectDetection={(d) => {
                  setSelectedDetection(d);
                  setFlyTarget({ center: [(d.bbox[1] + d.bbox[3]) / 2, (d.bbox[0] + d.bbox[2]) / 2], zoom: 12.5, key: Date.now() });
                }}
                selectedDetectionId={selectedDetection?.id ?? null}
                linkFullReport={`/verdicts/${encodeURIComponent(cachedResults.bundleId)}`}
              />
            </>
          );
        }
        return (
          <div className="p-3 text-xs text-[#8a837b]">
            nothing open yet — scan a tile or open a cached result from Explore.
          </div>
        );
      })()}
      {panel === "granules" && (
        <DataPanel
          tileId={tile?.id ?? selectedTile}
          tileBbox={tile?.bbox_lonlat ?? null}
          lon={pin ? pin[1] : null}
          lat={pin ? pin[0] : null}
          engineOnline={engineOnline}
          credentials={health?.earthdata_credentials}
          granuleSearchStatus={health?.granule_search?.status}
          granuleSearchReason={health?.granule_search?.reason ?? null}
          onFootprintOnMap={setPairFootprint}
        />
      )}
    </>
  );

  return (
    <div className={`globe-root fixed inset-0 bg-[#0b0d12]${activeOverlayLegend ? " legend-open" : ""}`}>
      <GlobeMap
        initialCamera={initialCam}
        flyTarget={flyTarget}
        pin={pin}
        tilePolygon={shownPolygon}
        pins={pins}
        selectedTile={selectedTile}
        overlays={overlaySpecs}
        pairFootprint={pairFootprint}
        detectionOutline={selectedDetection?.bbox ? bboxRing(selectedDetection.bbox) : null}
        flat={flat}
        reducedMotion={reducedMotion}
        idleSpin={!pin}
        onMapClick={onMapClick}
        onPinSelect={(t) => {
          const hit = pins.find((p) => p.tile === t);
          setSelectedTile(t);
          if (hit) selectPoint(hit.lat, hit.lon, { zoom: 11.5, panel: "pin" });
        }}
        onMove={(_lat, _lon, z) => {
          zoomRef.current = z;
          pushUrl({ z });
        }}
        onWebglUnavailable={setWebglMsg}
      />

      {webglMsg && (
        <div className="pointer-events-auto absolute left-1/2 top-1/2 z-30 w-[min(90vw,420px)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-[#e7e2da] bg-[#faf9f6] p-4 text-sm shadow-xl">
          <p className="font-semibold text-[#1c1a17]">The globe cannot start</p>
          <p className="mt-1 text-xs text-[#4a443c]">{webglMsg}</p>
          <p className="mt-1 text-xs text-[#8a837b]">
            the panel still works: search, lists, scan status, findings and the Data tab are usable
            without the map (the Data tab shows the timeline without the footprint inset).
          </p>
        </div>
      )}

      {/* top-left floating nav + search pill */}
      <div className="pointer-events-none absolute left-3 top-3 z-30 flex flex-col gap-2">
        <div className="pointer-events-auto w-fit rounded-xl border border-[#262c36] bg-[#0e1116]/95 px-3 py-2 shadow-lg backdrop-blur">
          <AppNav active="map" dark />
        </div>
        <SearchBar
          range={rangeInput}
          onRange={setRangeInput}
          rangeBad={rangeBad}
          rangeNote={rangeBad
            ? "Range must be 1–200 km, with the box inside ±84° latitude."
            : customBox
              ? `Box ${rangeInput} × ${rangeInput} km is drawn and used by Data retrieval. A scan still covers the engine's tile${tile ? ` ${tile.id}` : ""}.`
              : null}
          lat={latInput}
          lon={lonInput}
          onLat={(v) => {
            setLatInput(v);
            const parsed = Number(v);
            if (Number.isFinite(parsed) && lonInput && Number.isFinite(Number(lonInput))) {
              const pv = parseQuiet(`${v}, ${lonInput}`);
              if (pv) {
                setPin([pv.lat, pv.lon]);
                setFlyTarget({ center: [pv.lon, pv.lat], zoom: Math.max(zoomRef.current ?? 0, 10.5), key: Date.now() });
              }
            }
          }}
          onLon={(v) => {
            setLonInput(v);
            const parsed = Number(v);
            if (Number.isFinite(parsed) && latInput && Number.isFinite(Number(latInput))) {
              const pv = parseQuiet(`${latInput}, ${v}`);
              if (pv) {
                setPin([pv.lat, pv.lon]);
                setFlyTarget({ center: [pv.lon, pv.lat], zoom: Math.max(zoomRef.current ?? 0, 10.5), key: Date.now() });
              }
            }
          }}
          onSubmit={(lat, lon) => selectPoint(lat, lon, { zoom: 11.5, panel: "pin" })}
          onLocate={(lat, lon) => selectPoint(lat, lon, { zoom: 4.2 })}
          flat={flat}
          onToggleFlat={() => {
            setFlat((f) => {
              try {
                window.localStorage.setItem("globe-flat", f ? "0" : "1");
              } catch { /* private mode */ }
              return !f;
            });
          }}
          reducedMotion={reducedMotion}
        />
        {!engineOnline && healthChecked && (
          <p role="status" className="pointer-events-auto w-[min(92vw,340px)] rounded-xl border border-[#b08d57]/40 bg-[#f7f2e7]/95 px-3 py-2 text-[11px] text-[#7a6438] backdrop-blur">
            engine offline at <span className="mono">{engineUrl}</span> — Scan is disabled; cached
            results still open from Explore.
          </p>
        )}
      </div>

      {/* legend (bottom-left) */}
      <div className="pointer-events-none absolute bottom-3 left-3 z-30 hidden md:block">
        <Legend layer={activeOverlayLegend?.layer ?? null} opacity={activeOverlayLegend?.opacity ?? 0.8} />
      </div>

      {/* attribution/scale bottom-right is part of maplibre; nothing else needed */}

      {isNarrow ? (
        <BottomSheet ariaLabel="globe panel">{panelContent}</BottomSheet>
      ) : (
        <aside
          className="absolute bottom-3 right-3 top-3 z-30 flex w-[400px] flex-col overflow-hidden rounded-2xl border border-[#e7e2da] bg-[#faf9f6]/97 shadow-xl backdrop-blur"
          aria-label="globe panel"
        >
          <div className="flex-1 overflow-y-auto">{panelContent}</div>
        </aside>
      )}
    </div>
  );
}

function bboxRing(bbox: [number, number, number, number]): [number, number][] {
  const [w, s, e, n] = bbox;
  return [
    [w, s], [e, s], [e, n], [w, n], [w, s],
  ];
}

function parseQuiet(text: string): { lat: number; lon: number } | null {
  const out = parseCoordinates(text);
  return out.ok ? out.value : null;
}
