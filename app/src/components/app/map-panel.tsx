'use client';

/**
 * The map panel — Leaflet with the Mercator-exact raster overlay,
 * footprints, earthquake events, hover readout and legend.
 *
 * Before/After swipe mode (2026-10-24): a single map instance keeps zoom,
 * pan and geographic bounds synchronized across both sides BY CONSTRUCTION.
 * The BEFORE side shows the Esri World Imagery satellite basemap alone; the
 * AFTER side shows the same imagery with the measured change layer clipped
 * to the right of a draggable divider. The base imagery is a multi-date
 * satellite composite — the acquisition dates label the radar measurement,
 * not the base image (disclosed under the map).
 *
 * Pixel-exact divider (2026-10-24): the clip is computed in PIXELS in
 * Leaflet's layer coordinate space — containerPointToLayerPoint(swipeX% of
 * container width) minus the image element's own offset — and re-applied on
 * every drag, overlay re-render, mode toggle and map move/zoom/resize, so
 * the reveal edge stays glued to the handle at any zoom/pan (a percentage
 * clip-path would resolve against the image's own moving box and drift).
 *
 * Basemap: the same realistic satellite imagery is used in EVERY mode —
 * idle (before analyzing), swipe and single-layer — so the map the visitor
 * first sees already has the realism and terrain depth of the post-analysis
 * view. A sparse Esri reference layer (place names, boundaries) sits on top
 * so the photo stays orientable without giving up realism.
 */

import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { MapContainer, TileLayer, Polygon, CircleMarker, ImageOverlay, Tooltip, ScaleControl, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { ChevronsLeftRight, HelpCircle } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { DatasetDetail, PanamaGranule } from './types';
import type { UsgsEventLite } from '@/lib/science/types';
import type { DecodedGrids } from './decode';
import type { LayerKind, RenderedOverlay } from './raster-render';
import { renderOverlay, sampleAt, gridBounds } from './raster-render';
import { displacementColor, phaseColor } from './colormap';

export interface BeforeAfterInfo {
  /** Radar acquisition dates that bracket the measured change. */
  refTime: string;
  secTime: string;
  /** True when the overlay is a forward-model prediction, not a measurement. */
  prediction: boolean;
}

export interface MapPanelProps {
  dataset: DatasetDetail;
  /** Decoded transport grids (quantitative result) */
  grids?: DecodedGrids | null;
  gridsMeta?: RenderedOverlay extends never ? never : {
    xStart: number; xSpacing: number; yStart: number; ySpacing: number;
    width: number; height: number; epsg: number;
  };
  layer: LayerKind;
  opacity: number;
  events: UsgsEventLite[];
  primaryEvent?: UsgsEventLite | null;
  /** Granule footprints (evidence mode) */
  granulePolys?: Array<{ granule: PanamaGranule; poly: [number, number][] }>;
  /** Before/After acquisition info — enables the swipe divider. */
  beforeAfter?: BeforeAfterInfo | null;
  /** Radar wavelength (metres) — used by the advanced phase layer. */
  wavelengthM?: number;
  /** Swipe mode toggle (page-level mapMode state). */
  swipe?: boolean;
}

function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
function fmtDayShort(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function ZoomControls({ belowChip }: { belowChip?: boolean }) {
  const map = useMap();
  return (
    <div className={`absolute right-3 z-[500] flex flex-col overflow-hidden rounded-md border border-[#E7E2DA] bg-white ${belowChip ? 'top-11' : 'top-3'}`}>
      <button
        type="button"
        aria-label="Zoom in"
        onClick={() => map.zoomIn()}
        className="flex h-7 w-7 items-center justify-center text-[16px] leading-none text-[#4A443C] transition-colors hover:bg-[#F7F4EE]"
      >
        +
      </button>
      <div className="h-px bg-[#E7E2DA]" />
      <button
        type="button"
        aria-label="Zoom out"
        onClick={() => map.zoomOut()}
        className="flex h-7 w-7 items-center justify-center text-[16px] leading-none text-[#4A443C] transition-colors hover:bg-[#F7F4EE]"
      >
        −
      </button>
    </div>
  );
}

function FitBounds({ polygon }: { polygon: [number, number][] }) {
  // `polygon` arrives as [lat, lon] pairs (already normalised by the caller).
  const map = useMap();
  useEffect(() => {
    if (polygon.length > 2) {
      const bounds = L.latLngBounds(polygon as L.LatLngExpression[]);
      map.fitBounds(bounds.pad(0.25));
    }
  }, [map, polygon]);
  return null;
}

/**
 * Re-applies the swipe clip on every map interaction. Takes a REF to the
 * (swipeX-dependent) clip function so the Leaflet event subscriptions stay
 * stable across drags.
 */
function ClipSync({ clipRef }: { clipRef: React.MutableRefObject<() => void> }) {
  useMapEvents({
    move: () => clipRef.current(),
    zoomend: () => clipRef.current(),
    resize: () => clipRef.current(),
    overlayadd: () => clipRef.current(),
  });
  return null;
}

function HoverReadout({
  grids,
  gridsMeta,
  layer,
  wavelengthM,
}: {
  grids: DecodedGrids | null;
  gridsMeta?: MapPanelProps['gridsMeta'];
  layer: LayerKind;
  wavelengthM?: number;
}) {
  const [pos, setPos] = useState<{ lat: number; lon: number } | null>(null);
  const [sample, setSample] = useState<{ displacement: number; coherence: number; sigma: number; detected: boolean } | undefined>();
  const lastRef = useRef(0);
  useMapEvents({
    mousemove(e) {
      const now = performance.now();
      if (now - lastRef.current < 33) return;
      lastRef.current = now;
      setPos({ lat: e.latlng.lat, lon: e.latlng.lng });
      if (grids && gridsMeta) {
        setSample(sampleAt(grids, gridsMeta, e.latlng.lat, e.latlng.lng));
      } else {
        setSample(undefined);
      }
    },
    mouseout() {
      setPos(null);
      setSample(undefined);
    },
  });
  if (!pos) return null;
  const fmt = (v: number | undefined, unit: string, scale = 1000, digits = 1) =>
    v === undefined || Number.isNaN(v) ? '—' : `${(v * scale).toFixed(digits)} ${unit}`;
  let value = '—';
  let label = '';
  if (sample) {
    if (layer === 'velocity') {
      const mmYr = sample.displacement * 1000;
      value = `${mmYr >= 0 ? '+' : ''}${mmYr.toFixed(1)} mm/yr`;
      label = mmYr >= 0 ? 'moving away from satellite' : 'moving toward satellite';
    } else if (layer === 'detected') {
      if (sample.detected) {
        const mm = sample.displacement * 1000;
        value = `${mm >= 0 ? '+' : '−'}${Math.abs(mm).toFixed(1)} mm`;
        label = mm >= 0 ? 'ground moved away (sank)' : 'ground moved toward (rose)';
      } else {
        value = 'no change';
        label = 'below the detection threshold';
      }
    } else if (layer === 'phase') {
      const mm = sample.displacement * 1000;
      const half = wavelengthM ? (wavelengthM / 2 * 100).toFixed(1) : '12';
      value = `${Math.abs(mm).toFixed(1)} mm`;
      label = `one full color cycle ≈ ${half} cm`;
    } else if (layer === 'displacement' || layer === 'coverage') {
      const mm = sample.displacement * 1000;
      value = `${Math.abs(mm).toFixed(1)} mm`;
      label = mm >= 0 ? 'away from satellite' : 'toward satellite';
    } else if (layer === 'coherence') {
      value = Number.isFinite(sample.coherence) ? sample.coherence.toFixed(2) : '—';
      label = 'signal quality (0–1)';
    } else {
      value = fmt(sample.sigma, 'mm');
      label = 'uncertainty';
    }
  }
  return (
    <div
      className="absolute left-3 top-3 z-[500] rounded-lg border bg-white/95 px-3 py-2 font-mono text-[11px] leading-4 text-[#4A443C] shadow-sm"
      aria-live="polite"
    >
      <div>{pos.lat.toFixed(4)}°, {pos.lon.toFixed(4)}°</div>
      <div className="text-[#1C1A17]">
        {value}
        {label && <span className="ml-1.5 font-sans text-[10px] text-[#8A837B]">{label}</span>}
      </div>
    </div>
  );
}

function Legend({ layer, scaleMaxM, wavelengthM }: { layer: LayerKind; scaleMaxM?: number; wavelengthM?: number }) {
  if (layer === 'coverage') {
    return (
      <div className="absolute bottom-3 left-3 z-[500] rounded-lg border bg-white/95 px-3 py-2 shadow-sm">
        <div className="mb-1 text-[10px] uppercase tracking-[0.08em] text-[#8A837B]">Where measurements exist</div>
        <div className="flex items-center gap-2">
          <span className="h-2 w-8 rounded-sm border border-[#E7E2DA]" style={{ background: 'rgb(148,140,129)' }} />
          <span className="font-mono text-[10px] text-[#6F6862]">measured</span>
        </div>
        <div className="mt-1 text-[10px] text-[#8A837B]">transparent = signal too weak to measure</div>
      </div>
    );
  }
  if (layer === 'detected') {
    return (
      <div className="absolute bottom-3 left-3 z-[500] rounded-lg border bg-white/95 px-3 py-2 shadow-sm">
        <div className="mb-1 text-[10px] uppercase tracking-[0.08em] text-[#8A837B]">Detected change — high confidence</div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-4 rounded-sm border border-[#E7E2DA]" style={{ background: displacementColor(-0.8) }} />
            <span className="font-mono text-[10px] text-[#6F6862]">ground rose</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-4 rounded-sm border border-[#E7E2DA]" style={{ background: displacementColor(0.8) }} />
            <span className="font-mono text-[10px] text-[#6F6862]">ground sank</span>
          </span>
        </div>
        <div className="mt-1 text-[10px] text-[#8A837B]">
          only areas where the measured change is bigger than the uncertainty are colored
        </div>
      </div>
    );
  }
  if (layer === 'phase') {
    const half = wavelengthM ? (wavelengthM / 2 * 100).toFixed(1) : '12';
    const gradient = `linear-gradient(to right, ${[0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1]
      .map((f) => phaseColor(f))
      .join(', ')})`;
    return (
      <div className="absolute bottom-3 left-3 z-[500] rounded-lg border bg-white/95 px-3 py-2 shadow-sm">
        <div className="mb-1 text-[10px] uppercase tracking-[0.08em] text-[#8A837B]">Radar phase — advanced view</div>
        <div className="flex items-center gap-2">
          <div className="h-2 w-32 rounded-sm border border-[#E7E2DA]" style={{ background: gradient }} />
        </div>
        <div className="mt-1 text-[10px] text-[#8A837B]">
          like contour lines: each full color cycle ≈ {half} cm of ground movement · the plain movement map is computed from this
        </div>
      </div>
    );
  }
  const isVel = layer === 'velocity';
  const gradient =
    layer === 'displacement' || isVel
      ? `linear-gradient(to right, ${displacementColor(-1)}, ${displacementColor(-0.34)}, ${displacementColor(0)}, ${displacementColor(0.34)}, ${displacementColor(1)})`
      : layer === 'coherence'
        ? `linear-gradient(to right, #F5F1E8, #CBC3B5, #8A837A, #4A443C)`
        : `linear-gradient(to right, #F5F1E8, #E2C7AC, #B0714E, #7A4A2B)`;
  const left =
    layer === 'displacement' || isVel
      ? isVel
        ? `${((scaleMaxM ?? 0.05) * 1000).toFixed(0)} mm/yr toward`
        : `${((scaleMaxM ?? 0.05) * 100).toFixed(0)} cm toward`
      : layer === 'coherence'
        ? 'weak signal'
        : 'low';
  const right =
    layer === 'displacement' || isVel
      ? isVel
        ? `${((scaleMaxM ?? 0.05) * 1000).toFixed(0)} mm/yr away`
        : `${((scaleMaxM ?? 0.05) * 100).toFixed(0)} cm away`
      : layer === 'coherence'
        ? 'strong signal'
        : 'high';
  const title =
    layer === 'displacement' || isVel
      ? isVel
        ? 'LOS velocity (sustained motion)'
        : 'Movement toward / away from the satellite'
      : layer === 'coherence'
        ? 'Signal quality (coherence)'
        : 'Measurement uncertainty';
  return (
    <div className="absolute bottom-3 left-3 z-[500] rounded-lg border bg-white/95 px-3 py-2 shadow-sm">
      <div className="mb-1 text-[10px] uppercase tracking-[0.08em] text-[#8A837B]">{title}</div>
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-[#6F6862]">{left}</span>
        <div className="relative h-2 w-32">
          <div className="absolute inset-0 rounded-sm border border-[#E7E2DA]" style={{ background: gradient }} />
          {(layer === 'displacement' || isVel) && (
            <div className="absolute inset-y-0 left-1/2 w-px bg-[#4A443C]/60" aria-hidden />
          )}
        </div>
        <span className="font-mono text-[10px] text-[#6F6862]">{right}</span>
      </div>
      <div className="mt-1 text-[10px] text-[#8A837B]">
        {layer === 'displacement' || isVel
          ? isVel
            ? 'brown = moving toward the satellite · green = away from it · pale = stable'
            : 'brown = moved toward the satellite (mostly rising) · green = away (mostly sinking) · pale = little or no change'
          : layer === 'coherence'
            ? 'darker = more reliable measurement'
            : 'darker = more uncertain measurement'}
      </div>
      {(layer === 'displacement' || isVel) && (
        <div className="mt-0.5 text-center font-mono text-[9.5px] text-[#8A837B]">0 = no change</div>
      )}
    </div>
  );
}

function HelpHeading({ children }: { children: React.ReactNode }) {
  return <h4 className="mt-3 text-[11px] font-semibold uppercase tracking-[0.06em] text-[#8A837B]">{children}</h4>;
}

/**
 * Plain-language explainer for the map — written for someone who has never
 * heard of radar interferometry. One short answer per question a first-time
 * visitor asks: what is the picture, what does the divider do, when were the
 * data taken, what do the colors mean, and what is this NOT.
 */
function WhatAmISeeing({
  beforeAfter,
  layer,
  wavelengthM,
}: {
  beforeAfter: BeforeAfterInfo | null;
  layer: LayerKind;
  wavelengthM?: number;
}) {
  const swipe = !!beforeAfter;
  const prediction = beforeAfter?.prediction ?? false;
  let colors: React.ReactNode;
  if (layer === 'displacement' || layer === 'velocity') {
    colors = (
      <>
        <span className="font-semibold text-[#7A4A2B]">Brown</span> — that patch of ground moved{' '}
        <em>toward</em> the satellite (mostly rising).{' '}
        <span className="font-semibold text-[#2F5D53]">Green</span> — it moved <em>away</em> (mostly sinking).
        Pale means little or no change.
      </>
    );
  } else if (layer === 'detected') {
    colors = (
      <>
        Solid color marks only the areas where the measured change is bigger than the uncertainty — a real change
        with high confidence. <span className="font-semibold text-[#7A4A2B]">Brown</span> = ground rose,{' '}
        <span className="font-semibold text-[#2F5D53]">green</span> = ground sank. Nothing else is colored.
      </>
    );
  } else if (layer === 'coherence') {
    colors = <>Darker areas have a stronger, more reliable radar signal. Very light areas could not be measured at all (open water, dense forest…).</>;
  } else if (layer === 'sigma') {
    colors = <>Darker = more uncertain measurement. A change smaller than this cannot be told apart from noise.</>;
  } else if (layer === 'phase') {
    const half = wavelengthM ? (wavelengthM / 2 * 100).toFixed(1) : '12';
    colors = (
      <>
        The raw radar measurement drawn as color fringes, like contour lines on a hiking map — each full color cycle
        represents ≈ {half} cm of ground movement. The plain movement map is computed from this. Advanced view; you
        don&rsquo;t need it to understand the result.
      </>
    );
  } else {
    colors = <>Gray shows where a valid measurement exists. Transparent areas were excluded — no reliable signal there.</>;
  }
  const H = HelpHeading;
  return (
    <div className="max-h-[65vh] overflow-y-auto px-4 py-3.5">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-[#1C1A17]">
        <HelpCircle className="h-4 w-4 text-[#7A6438]" aria-hidden />
        What am I seeing?
      </div>
      {swipe ? (
        <>
          <H>The picture</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">
            A satellite photo of the study area — the <strong>same photo on both sides</strong> of the divider, so you can
            recognize coastlines, rivers and cities. The divider doesn&rsquo;t move the map: it reveals the layer that
            was computed from radar.
          </p>
          <H>The two dates</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">
            NASA&rsquo;s radar satellite passed over this area twice —{' '}
            <strong>{fmtDay(beforeAfter?.refTime ?? '')}</strong> and <strong>{fmtDay(beforeAfter?.secTime ?? '')}</strong>. The
            analysis compares those two radar snapshots and measures how the ground changed in between.
          </p>
          <H>The colors</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">{colors}</p>
          <H>What this is not</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">
            {prediction ? (
              <>
                Not a measurement of this event: the colored layer is what a physics model <em>predicts</em> from the
                published USGS fault mechanism — clearly labelled as a prediction. The photo behind it is a multi-date
                satellite composite (Esri).
              </>
            ) : (
              <>
                Not a photo of the change itself. The photo is a multi-date satellite composite (Esri); the colored layer
                is computed from the two radar scans. Transparent areas were not measured.
              </>
            )}
          </p>
          <H>Try this</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">
            Drag the round handle (or focus it and use ← → keys). Move the mouse over the map to read exact values.
            For the plainest highlight of changed areas, switch the layer tabs below the map to{' '}
            <strong>Detected change</strong>.
          </p>
        </>
      ) : (
        <>
          <H>The picture</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">
            A satellite photo of the study area with a colored measurement layer on top.
          </p>
          <H>The colors</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">{colors}</p>
          <H>What this is not</H>
          <p className="mt-1 text-[12px] leading-[1.5rem] text-[#4A443C]">
            Not a photo of the change itself — the colored layer is computed from radar scans, the photo behind it is a
            multi-date satellite composite (Esri).
          </p>
        </>
      )}
    </div>
  );
}

export default function MapPanel(props: MapPanelProps) {
  const { dataset, grids, gridsMeta, layer, opacity, events, primaryEvent, granulePolys, beforeAfter, wavelengthM, swipe } = props;
  const [overlay, setOverlay] = useState<RenderedOverlay | null>(null);
  const [rendering, setRendering] = useState(false);
  const [swipeX, setSwipeX] = useState(50);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);
  const overlayInstanceRef = useRef<L.ImageOverlay | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const [hintOpen, setHintOpen] = useState(false);
  const [hintSeen, setHintSeen] = useState(true); // SSR-safe: hidden until mounted
  const swipeActive = !!swipe && !!beforeAfter;

  // The one-time "drag to compare" bubble: shows once per browser session,
  // disappears after the first drag or 12 s.
  const dismissHint = useCallback(() => {
    setHintSeen(true);
    try {
      sessionStorage.setItem('motus.swipe-hint', '1');
    } catch {
      /* private mode — ignore */
    }
  }, []);
  useEffect(() => {
    try {
      setHintSeen(sessionStorage.getItem('motus.swipe-hint') === '1');
    } catch {
      setHintSeen(false);
    }
  }, []);
  useEffect(() => {
    if (!swipeActive || hintSeen) return;
    const t = setTimeout(dismissHint, 12000);
    return () => clearTimeout(t);
  }, [swipeActive, hintSeen, dismissHint]);

  // Overlay rendering (Mercator-exact warp) with a paint-friendly deferral.
  useEffect(() => {
    if (!grids || !gridsMeta) {
      setOverlay(null);
      return;
    }
    setRendering(true);
    const t = setTimeout(() => {
      try {
        const ov = renderOverlay(grids, gridsMeta, layer, undefined, 1024, wavelengthM);
        setOverlay(ov ?? null);
      } catch (e) {
        console.error('[motus-overlay] render failed', e);
        setOverlay(null);
      } finally {
        setRendering(false);
      }
    }, 10);
    return () => clearTimeout(t);
  }, [grids, gridsMeta, layer, wavelengthM]);

  // ---- Pixel-exact swipe clip ---------------------------------------------------
  // The reveal edge must sit exactly under the divider handle at any zoom,
  // pan and overlay geometry. Percentages resolve against the image element's
  // own (moving) box — so the clip is computed in PIXELS in Leaflet's layer
  // coordinate space and re-applied on every map interaction.
  const applyClip = useCallback(() => {
    const map = mapRef.current;
    const img = overlayInstanceRef.current?.getElement() ?? null;
    if (!map || !img) return;
    if (!swipeActive) {
      img.style.clipPath = '';
      return;
    }
    const rect = map.getContainer().getBoundingClientRect();
    const px = (swipeX / 100) * rect.width;
    const layerX = map.containerPointToLayerPoint([px, 0]).x;
    const imgX = L.DomUtil.getPosition(img).x;
    const edge = Math.max(0, layerX - imgX);
    img.style.clipPath = `inset(0px 0px 0px ${edge}px)`;
  }, [swipeActive, swipeX]);
  const applyClipRef = useRef(applyClip);
  applyClipRef.current = applyClip;

  // Re-apply whenever the clip inputs change (drag, overlay swap, mode toggle).
  useEffect(() => {
    if (!swipeActive) return;
    const raf = requestAnimationFrame(() => applyClipRef.current());
    return () => cancelAnimationFrame(raf);
  }, [swipeActive, swipeX, overlay, layer]);

  // Dev-only handle for E2E pan/zoom verification of the clip alignment.
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production') {
      (window as unknown as { __motusMap?: L.Map | null }).__motusMap = mapRef.current;
    }
  });

  // Divider dragging: pointer events with capture + keyboard support.
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    setSwipeX(Math.min(98.5, Math.max(1.5, x)));
  }, []);
  const stopDrag = useCallback(() => {
    draggingRef.current = false;
    window.removeEventListener('pointermove', onPointerMove as unknown as EventListener);
    window.removeEventListener('pointerup', stopDrag);
    window.removeEventListener('pointercancel', stopDrag);
  }, [onPointerMove]);
  const startDrag = useCallback(
    (e: React.PointerEvent) => {
      draggingRef.current = true;
      setHintSeen(true);
      try {
        sessionStorage.setItem('motus.swipe-hint', '1');
      } catch {
        /* ignore */
      }
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      window.addEventListener('pointermove', onPointerMove as unknown as EventListener);
      window.addEventListener('pointerup', stopDrag);
      window.addEventListener('pointercancel', stopDrag);
      if (e.currentTarget instanceof HTMLElement) e.currentTarget.focus({ preventScroll: true });
    },
    [onPointerMove, stopDrag],
  );

  const footprint: [number, number][] = useMemo(
    () => (dataset.footprintPolygon ?? []).map(([lon, lat]) => [lat, lon] as [number, number]),
    [dataset.footprintPolygon],
  );

  return (
    <div
      ref={containerRef}
      className="relative overflow-hidden rounded-xl border border-[#E7E2DA] bg-white"
      style={{ minHeight: 420 }}
      role="region"
      aria-label="Study area map"
    >
      <MapContainer
        ref={mapRef}
        center={[dataset.center?.lat ?? 0, dataset.center?.lon ?? 0]}
        zoom={dataset.center ? 8 : 2}
        style={{ height: '100%', minHeight: 420, position: 'absolute', inset: 0 }}
        zoomControl={false}
        attributionControl={true}
      >
        <ZoomControls belowChip={swipeActive} />
        {/* Real satellite imagery in every mode — the default (pre-analysis)
            view gets the same realism and terrain depth as the result view. */}
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          attribution="Imagery &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics"
        />
        {/* Sparse place names + boundaries over the imagery, so coastlines,
            cities and borders stay recognizable without a flat street map. */}
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
          attribution="Labels &copy; Esri"
        />
        <ScaleControl position="bottomright" metric={true} imperial={false} />
        <FitBounds polygon={footprint} />
        <ClipSync clipRef={applyClipRef} />
        {overlay && (
          <ImageOverlay
            ref={overlayInstanceRef as never}
            url={overlay.dataUrl}
            bounds={[[overlay.geo.latMin, overlay.geo.lonMin], [overlay.geo.latMax, overlay.geo.lonMax]]}
            opacity={opacity}
          />
        )}
          {/* Footprint: soft dark casing under a white line, so the frame
              reads on bright desert, dark ocean and colored overlays alike. */}
          <Polygon
            positions={footprint.length > 2 ? footprint : []}
            pathOptions={{ color: '#1C1A17', weight: 3.5, opacity: 0.55, fill: false, interactive: false }}
          />
          <Polygon
            positions={footprint.length > 2 ? footprint : []}
            pathOptions={{ color: '#FFFFFF', weight: 1.5, fillColor: '#FFFFFF', fillOpacity: 0.05 }}
          />
        {granulePolys?.map(({ granule, poly }) => (
          <Polygon
            key={granule.granuleId}
            positions={poly.map(([lon, lat]) => [lat, lon] as [number, number])}
            pathOptions={{ color: '#B08D57', weight: 1, dashArray: '4 3', fillOpacity: 0.02 }}
          >
            <Tooltip>{granule.granuleId.slice(0, 52)}…</Tooltip>
          </Polygon>
        ))}
        {events.map((ev) => {
          const primary = primaryEvent && ev.id === primaryEvent.id;
          return (
            <CircleMarker
              key={ev.id}
              center={[ev.lat, ev.lon]}
              radius={4 + Math.max(0, ev.magnitude - 4) * 2.2}
              pathOptions={{
                color: primary ? '#6E2418' : '#9E3B2C',
                weight: primary ? 2.5 : 1,
                fillColor: '#9E3B2C',
                fillOpacity: 0.8,
              }}
            >
              <Tooltip>
                <div className="text-[11px] leading-4">
                  <div className="font-semibold">M{ev.magnitude.toFixed(1)} {ev.magType ?? ''}</div>
                  <div>{ev.place}</div>
                  <div>{ev.time.slice(0, 16).replace('T', ' ')} UTC · depth {ev.depthKm.toFixed(0)} km</div>
                  <a href={ev.url} target="_blank" rel="noopener noreferrer" className="underline">
                    USGS event page
                  </a>
                </div>
              </Tooltip>
            </CircleMarker>
          );
        })}
        <HoverReadout grids={grids ?? null} gridsMeta={gridsMeta} layer={layer} wavelengthM={wavelengthM} />
      </MapContainer>

      {/* ---- Before / After swipe chrome ---- */}
      {swipeActive && beforeAfter && (
        <>
          <div
            className="pointer-events-none absolute inset-y-0 z-[540]"
            style={{ left: `${swipeX}%` }}
            aria-hidden
          >
            <div className="absolute inset-y-0 -left-px w-0.5 bg-white shadow-[0_0_6px_rgba(28,26,23,0.65)]" />
          </div>
          <div
            className="absolute inset-y-0 z-[560] w-9 -translate-x-1/2 cursor-ew-resize"
            style={{ left: `${swipeX}%`, touchAction: 'none' }}
            role="slider"
            tabIndex={0}
            aria-label="Before/after divider position"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(swipeX)}
            onPointerDown={startDrag}
            onPointerUp={stopDrag}
            onPointerMove={onPointerMove}
            onKeyDown={(e) => {
              if (e.key === 'ArrowLeft') { setHintSeen(true); setSwipeX((x) => Math.max(1.5, x - 2)); }
              if (e.key === 'ArrowRight') { setHintSeen(true); setSwipeX((x) => Math.min(98.5, x + 2)); }
            }}
          >
            <div className="absolute left-1/2 top-1/2 flex h-9 w-9 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-[#E7E2DA] bg-white shadow-[0_2px_10px_rgba(28,26,23,0.35)]">
              <ChevronsLeftRight className="h-4 w-4 text-[#1C1A17]" aria-hidden />
            </div>
          </div>
          {!hintSeen && (
            <div
              className="pointer-events-none absolute top-1/2 z-[555] -translate-y-1/2 animate-pulse whitespace-nowrap rounded-full bg-[#1C1A17]/90 px-3 py-1.5 text-[11px] font-medium text-white shadow-md"
              style={
                swipeX > 60
                  ? { right: `calc(${100 - swipeX}% + 26px)` }
                  : { left: `calc(${swipeX}% + 26px)` }
              }
            >
              ← drag to compare →
            </div>
          )}
          <div
            className="pointer-events-none absolute left-3 top-3 z-[550] flex items-center gap-1.5 rounded-md bg-[#1C1A17]/85 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.08em] text-white backdrop-blur-sm"
          >
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#D8D2C6]" aria-hidden />
            Before · <span className="sm:hidden">{fmtDayShort(beforeAfter.refTime)}</span>
            <span className="hidden sm:inline">{fmtDay(beforeAfter.refTime)}</span>
          </div>
          <div
            className="pointer-events-none absolute right-3 top-3 z-[550] flex items-center gap-1.5 rounded-md bg-[#1C1A17]/85 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.08em] text-white backdrop-blur-sm"
          >
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#B08D57]" aria-hidden />
            After · <span className="sm:hidden">{fmtDayShort(beforeAfter.secTime)}</span>
            <span className="hidden sm:inline">{fmtDay(beforeAfter.secTime)}</span>
            <span className="hidden sm:inline">+ {beforeAfter.prediction ? 'model prediction' : 'measured change'}</span>
          </div>
        </>
      )}

      {/* ---- Plain-language "What am I seeing?" helper ---- */}
      {grids && (
        <Popover open={hintOpen} onOpenChange={setHintOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              className={`absolute left-1/2 z-[555] flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-[#E7E2DA] bg-white/95 px-3 py-1.5 text-[11px] font-medium text-[#1C1A17] shadow-sm backdrop-blur-sm transition-colors hover:bg-white ${swipeActive ? 'top-11' : 'top-3'}`}
              aria-label="What am I seeing? Plain-language explanation of this map"
            >
              <HelpCircle className="h-3.5 w-3.5 text-[#7A6438]" aria-hidden />
              What am I seeing?
            </button>
          </PopoverTrigger>
          <PopoverContent align="center" side="bottom" className="w-[320px] p-0 sm:w-[380px]">
            <WhatAmISeeing beforeAfter={swipeActive ? beforeAfter : null} layer={layer} wavelengthM={wavelengthM} />
          </PopoverContent>
        </Popover>
      )}

      {dataset.availability.state !== 'available' && (
        <div className="pointer-events-none absolute inset-x-0 top-1/2 z-[500] -translate-y-1/2 px-6 text-center">
          <p className="mx-auto max-w-md rounded-lg border border-[#E5C4B7] bg-[#FBF3EF]/95 px-4 py-3 text-[12px] leading-[1.4rem] text-[#9E3B2C]">
            The data for this area have not been downloaded to this server, so there is nothing to
            show on the map yet. The panel alongside explains how to obtain them.
          </p>
        </div>
      )}
      {overlay && <Legend layer={layer} scaleMaxM={overlay.scaleMaxM} wavelengthM={wavelengthM} />}
      {rendering && (
        <div className="absolute right-3 top-14 z-[500] rounded-md border bg-white/95 px-2.5 py-1 text-[11px] text-[#6F6862] shadow-sm">
          Drawing the layer…
        </div>
      )}
    </div>
  );
}
