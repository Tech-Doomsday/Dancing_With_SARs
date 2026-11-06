"use client";

/** MapLibre globe wrapper (DEC1, maplibre-gl 5.24.0 pinned). Owns the map instance and every
 *  source/layer on it; the panel talks to it through props. Thin client: no tiling math here —
 *  polygons and bounds arrive from the engine (tile-at) or the cache.
 *
 *  S1-verified facts baked in: Esri maxzoom 23 + exact attribution; projection changes must wait
 *  for style load (setProjection before that throws); image-source overlays use DEFAULT linear
 *  resampling because `raster-resampling: "nearest"` renders black on maplibre 5.23/5.24 (the
 *  colorized canvas is pre-upscaled nearest-neighbour in lib/raster.ts instead). */
import { useEffect, useRef, useState } from "react";
import maplibregl, { type Map as MlMap, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { seamSplits } from "@/lib/raster";
import { overlaySpecIdOfSource } from "@/lib/overlayIds";
import { DEFAULT_CAMERA, initialCamera, loadCamera, saveCamera } from "@/lib/camera";
import "./globe-overrides.css";

const ESRI_ATTRIBUTION = "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community";
/** Terrain (3D): free AWS Terrain Tiles, "terrarium" RGB-encoded elevation, no API key. Used for the real 3D relief
 *  (map.setTerrain) and for a faint hillshade. Image/raster layers (the SAR image, result overlays) are DRAPED over the
 *  terrain by maplibre itself, so they follow the relief with no extra code. Pure display: nothing here is science. */
const DEM_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
const DEM_ATTRIBUTION = "Terrain: Mapzen, SRTM, USGS and other sources via AWS Terrain Tiles";
/** 3D buildings: OpenStreetMap footprints + heights from OpenFreeMap's free vector tiles (OpenMapTiles schema, no key).
 *  Real data, but many OSM buildings carry no height tag: those fall back to a flat 5 m so the footprint still shows. */
const BUILDINGS_URL = "https://tiles.openfreemap.org/planet";
const BUILDINGS_ATTRIBUTION = "OpenFreeMap © OpenMapTiles, data from OpenStreetMap";
const BUILDINGS_MIN_ZOOM = 13;
/** sky + horizon haze for tilted views; fades out before zoom 7 so the upright globe keeps its plain look at world scale */
const SKY: maplibregl.SkySpecification = {
  "sky-color": "#16324f", "horizon-color": "#8fa6bd", "fog-color": "#0b0d12",
  "fog-ground-blend": 0.6, "horizon-fog-blend": 0.5, "sky-horizon-blend": 0.5,
  "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0],
};
/** below this zoom the relief is too small to read, so 3D mode does not tilt the camera on its own */
const TILT_MIN_ZOOM = 8;
const TILT_PITCH = 55;
const MAX_PITCH_3D = 75;
const ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export interface OverlaySpec {
  id: string;            // unique per source|bundle|pipeline|layer
  dataUrl: string;
  bounds: [number, number, number, number]; // [W, S, E, N]
  opacity: number;
  /** georeferenced SAR image: the engine's pixel-edge corners [TL, TR, BR, BL] as [lon, lat]. When present
   *  they ARE the image-source coordinates (a UTM grid is rotated against the graticule, so a bbox would
   *  misplace it) and bounds is only a bookkeeping envelope. No seam split: the engine answers null corners
   *  (and the image is not drawn) for a clip that straddles +/-180. Never computed in the UI. */
  corners?: [[number, number], [number, number], [number, number], [number, number]];
}

export interface PinFeature {
  tile: string;
  lon: number;
  lat: number;
  tier: string;
  label: string;
}

export interface FlyTarget {
  center: [number, number];
  zoom: number;
  key: number;
}

export interface GlobeMapProps {
  flyTarget: FlyTarget | null;
  pin: [number, number] | null;
  tilePolygon: [number, number][] | null;
  pins: PinFeature[];
  selectedTile: string | null;
  overlays: OverlaySpec[];
  pairFootprint: [number, number][] | null;
  /** bump to fit the camera to pairFootprint (set only by the explicit draw button) */
  fitFootprintKey?: number;
  detectionOutline: [number, number][] | null;
  flat: boolean;
  /** 3D terrain on/off (relief + hillshade + tilt). Works on the globe and on the flat map. */
  terrain3d?: boolean;
  /** vertical exaggeration of the relief (1 = true scale) */
  terrainExaggeration?: number;
  /** extruded OpenStreetMap buildings (only drawn in 3D mode, from zoom 13) */
  buildings3d?: boolean;
  /** one-shot camera command from the 3D controls: set the tilt and/or rotate by degrees; a new `key` re-fires it */
  viewCmd?: { key: number; pitch?: number; rotateBy?: number } | null;
  /** a link that names a point: the map is CREATED here (no world-view frame, no fly-in) */
  startAt?: { lat?: number; lon?: number; z?: number };
  reducedMotion: boolean;
  idleSpin: boolean;
  onMapClick: (lat: number, lon: number) => void;
  onPinSelect: (tile: string) => void;
  onMove: (lat: number, lon: number, zoom: number) => void;
  onWebglUnavailable: (message: string) => void;
}

const STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "space", type: "background", paint: { "background-color": "#0b0d12" } }],
};

function ringToPolygon(ring: [number, number][]): GeoJSON.Feature<GeoJSON.Polygon> {
  const closed = ring.length && (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])
    ? [...ring, ring[0]] : ring;
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [closed as GeoJSON.Position[]] },
  };
}

/** crop a horizontal fraction of a colorized texture (data URL) — cached per (url, crop) so
 *  opacity changes never re-decode; the common non-seam case ([0, 1]) returns the input. */
const cropCache = new Map<string, string>();
async function cropDataUrl(dataUrl: string, crop: [number, number]): Promise<string> {
  if (crop[0] === 0 && crop[1] === 1) return dataUrl;
  const key = `${crop[0]}|${crop[1]}|${dataUrl.length}|${dataUrl.slice(-24)}`;
  const hit = cropCache.get(key);
  if (hit) return hit;
  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const el = new Image();
    el.onload = () => resolve(el);
    el.onerror = () => reject(new Error("overlay texture could not be decoded"));
    el.src = dataUrl;
  });
  const x0 = Math.max(0, Math.min(1, crop[0]));
  const x1 = Math.max(0, Math.min(1, crop[1]));
  const sx = Math.round(img.naturalWidth * x0);
  const sw = Math.max(1, Math.round(img.naturalWidth * (x1 - x0)));
  const canvas = document.createElement("canvas");
  canvas.width = sw;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("overlay texture could not be cropped");
  ctx.drawImage(img, sx, 0, sw, img.naturalHeight, 0, 0, sw, img.naturalHeight);
  const out = canvas.toDataURL();
  if (cropCache.size >= 48) cropCache.delete(cropCache.keys().next().value as string);
  cropCache.set(key, out);
  return out;
}

export default function GlobeMap(props: GlobeMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);
  const cbRef = useRef(props);
  cbRef.current = props;

  // ---- instance lifecycle (StrictMode-safe: remove() on cleanup; S1 verified) ----
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let map: MlMap;
    // create the map directly at the requested / last-seen camera (never world view -> fly)
    const start = initialCamera(cbRef.current.startAt ?? {}, loadCamera());
    const startedElsewhere = start.camera !== DEFAULT_CAMERA;
    try {
      map = new maplibregl.Map({
        container,
        style: STYLE,
        center: start.camera.center,
        zoom: start.camera.zoom,
        attributionControl: { compact: true },
        canvasContextAttributes: { preserveDrawingBuffer: true },
        maxPitch: 0,
      });
    } catch (e) {
      cbRef.current.onWebglUnavailable(
        e instanceof Error ? e.message : "the browser could not start WebGL for the globe",
      );
      return;
    }
    mapRef.current = map;
    // test hook: how many map instances this page has constructed (never read by app code)
    try { const w = window as unknown as { __nisarMapCreates?: number }; w.__nisarMapCreates = (w.__nisarMapCreates ?? 0) + 1; } catch { /* ignore */ }
    map.on("error", () => { /* tile errors are transient; the UI must not die on them */ });
    map.on("load", () => {
      map.addSource("esri", {
        type: "raster",
        tiles: [ESRI_TILES],
        tileSize: 256,
        maxzoom: 23,
        attribution: ESRI_ATTRIBUTION,
      });
      map.addLayer({ id: "esri", type: "raster", source: "esri" });
      // elevation: drives map.setTerrain (3D) and a faint hillshade. The hillshade sits ABOVE the imagery and the overlays
      // but BELOW the outlines and pins, so relief stays visible through a semi-transparent overlay and the pins stay on top.
      map.addSource("dem", {
        type: "raster-dem", tiles: [DEM_TILES], encoding: "terrarium", tileSize: 256, maxzoom: 15,
        attribution: DEM_ATTRIBUTION,
      });
      map.addLayer({
        id: "hillshade", type: "hillshade", source: "dem", layout: { visibility: "none" },
        paint: {
          "hillshade-exaggeration": 0.35,
          "hillshade-shadow-color": "#0b0d12", "hillshade-highlight-color": "#ffffff", "hillshade-accent-color": "#0b0d12",
        },
      });
      map.addSource("buildings", { type: "vector", url: BUILDINGS_URL, attribution: BUILDINGS_ATTRIBUTION });
      map.addLayer({
        id: "buildings-3d", type: "fill-extrusion", source: "buildings", "source-layer": "building",
        minzoom: BUILDINGS_MIN_ZOOM, layout: { visibility: "none" },
        paint: {
          "fill-extrusion-color": "#d8d2c6",
          "fill-extrusion-height": ["coalesce", ["get", "render_height"], 5],
          "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
          "fill-extrusion-opacity": 0.72,
          "fill-extrusion-vertical-gradient": true,
        },
      });
      map.addSource("tile-outline", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "tile-outline-line", type: "line", source: "tile-outline",
                     paint: { "line-color": "#ffffff", "line-width": 1.5, "line-opacity": 0.9 } });
      map.addLayer({ id: "tile-outline-fill", type: "fill", source: "tile-outline",
                     paint: { "fill-color": "#ffffff", "fill-opacity": 0.06 } });
      map.addSource("pins", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "pin-clusters", type: "circle", source: "pins", filter: ["has", "point_count"],
        paint: { "circle-color": "#2c5f58", "circle-radius": 14, "circle-stroke-width": 2, "circle-stroke-color": "#faf9f6" },
      });
      map.addLayer({
        id: "pin-count", type: "symbol", source: "pins", filter: ["has", "point_count"],
        layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 11 },
        paint: { "text-color": "#ffffff" },
      });
      map.addLayer({
        id: "pin-point", type: "circle", source: "pins", filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-color": ["get", "color"],
          "circle-radius": 7,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });
      map.addSource("pair-footprint", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "pair-footprint-fill", type: "fill", source: "pair-footprint",
                     paint: { "fill-color": "#ffd166", "fill-opacity": 0.12 } });
      map.addLayer({ id: "pair-footprint-line", type: "line", source: "pair-footprint",
                     paint: { "line-color": "#ffd166", "line-width": 3 } });
      map.addSource("detection-outline", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "detection-line", type: "line", source: "detection-outline",
                     paint: { "line-color": "#9e3b2c", "line-width": 2.5, "line-dasharray": [2, 1.5] } });
      // §7.2 bottom-right: zoom + compass, scale, attribution — with the engine's Esri
      // attribution already bound to the raster source
      map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), "top-right");
      map.addControl(new maplibregl.ScaleControl({ maxWidth: 96, unit: "metric" }), "top-right");
      const c0 = map.getCenter().wrap();      // remember the camera the map actually opened at
      saveCamera({ center: [c0.lng, c0.lat], zoom: map.getZoom(), projection: cbRef.current.flat ? "mercator" : "globe" });
      setReady(true);
    });
    map.on("click", (e) => {
      const feats = map.queryRenderedFeatures(e.point, { layers: ["pin-point"] });
      const hit = feats[0]?.properties?.tile;
      if (typeof hit === "string" && hit) {
        cbRef.current.onPinSelect(hit);
        return;
      }
      cbRef.current.onMapClick(e.lngLat.lat, e.lngLat.wrap().lng);
    });
    map.on("moveend", () => {
      const c = map.getCenter().wrap();
      saveCamera({ center: [c.lng, c.lat], zoom: map.getZoom(), projection: cbRef.current.flat ? "mercator" : "globe" });
      cbRef.current.onMove(c.lat, c.lng, map.getZoom());
    });
    // idle spin (DEC2): slow, stopped by any interaction; not when reduced motion is requested
    let spinRAF = 0;
    const startSpin = () => {
      if (cbRef.current.reducedMotion || !cbRef.current.idleSpin) return;
      const step = () => {
        const m = mapRef.current;
        if (!m) return;
        const c = m.getCenter();
        c.lng += 0.02 * Math.max(0.25, Math.min(2, 60 / (m.getZoom() + 1)));
        m.setCenter(c.wrap());
        spinRAF = requestAnimationFrame(step);
      };
      spinRAF = requestAnimationFrame(step);
    };
    const stopSpin = () => {
      if (spinRAF) cancelAnimationFrame(spinRAF);
      spinRAF = 0;
    };
    // a map opened at a requested/remembered camera must stay there: no idle spin
    if (!startedElsewhere) map.once("load", () => startSpin());
    const userInteracted = () => stopSpin();
    map.on("mousedown", userInteracted);
    map.on("touchstart", userInteracted);
    map.on("wheel", userInteracted);
    map.on("dragstart", userInteracted);
    return () => {
      stopSpin();
      try {      // leaving the page (tab switch) keeps the camera for the next map
        const c = map.getCenter().wrap();
        saveCamera({ center: [c.lng, c.lat], zoom: map.getZoom(), projection: cbRef.current.flat ? "mercator" : "globe" });
      } catch { /* map already gone */ }
      setReady(false);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ---- projection toggle (S1: wait for the style; never call setProjection early) ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      try {
        map.setProjection({ type: props.flat ? "mercator" : "globe" });
      } catch { /* the map went away mid-flight */ }
    };
    if (map.isStyleLoaded()) apply();
    else map.once("style.load", apply);
  }, [props.flat, ready]);

  // ---- 3D terrain: real relief + hillshade + tilt, on the globe AND on the flat map ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    try {
      if (props.terrain3d) {
        map.setMaxPitch(MAX_PITCH_3D);
        map.setTerrain({ source: "dem", exaggeration: props.terrainExaggeration ?? 1.5 });
        if (map.getLayer("hillshade")) map.setLayoutProperty("hillshade", "visibility", "visible");
        map.setSky(SKY);
        // only tilt once the relief is large enough to read; at world zoom the globe stays upright
        if (map.getZoom() >= TILT_MIN_ZOOM && map.getPitch() < 20) {
          if (props.reducedMotion) map.setPitch(TILT_PITCH);
          else map.easeTo({ pitch: TILT_PITCH, duration: 900 });
        }
      } else {
        map.setTerrain(null);
        map.setSky(undefined as unknown as maplibregl.SkySpecification);   // empty = back to the plain space background (the runtime resets to transparent)
        if (map.getLayer("hillshade")) map.setLayoutProperty("hillshade", "visibility", "none");
        if (map.getPitch() > 0 && !props.reducedMotion) {
          map.easeTo({ pitch: 0, bearing: 0, duration: 600 });
          map.once("moveend", () => { if (!cbRef.current.terrain3d) map.setMaxPitch(0); });
        } else {
          map.setPitch(0);
          map.setMaxPitch(0);
        }
      }
    } catch { /* the map went away mid-flight, or the style is not ready: the next change re-applies */ }
  }, [props.terrain3d, props.terrainExaggeration, ready]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- 3D buildings: only while 3D is on ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("buildings-3d")) return;
    map.setLayoutProperty("buildings-3d", "visibility", props.terrain3d && props.buildings3d ? "visible" : "none");
  }, [props.terrain3d, props.buildings3d, ready]);

  // ---- view presets / rotate (for trackpads and phones, where right-drag tilting is awkward) ----
  const lastViewCmd = useRef(0);
  useEffect(() => {
    const map = mapRef.current;
    const cmd = props.viewCmd;
    if (!map || !ready || !cmd || cmd.key === lastViewCmd.current) return;
    lastViewCmd.current = cmd.key;
    const opts: { pitch?: number; bearing?: number; duration: number } = { duration: props.reducedMotion ? 0 : 600 };
    if (cmd.pitch !== undefined) {
      if (cmd.pitch > 0) map.setMaxPitch(MAX_PITCH_3D);
      opts.pitch = Math.max(0, Math.min(MAX_PITCH_3D, cmd.pitch));
      if (cmd.pitch === 0) opts.bearing = 0;           // "top-down" also puts north up
    }
    if (cmd.rotateBy !== undefined) opts.bearing = map.getBearing() + cmd.rotateBy;
    map.easeTo(opts);
  }, [props.viewCmd, ready, props.reducedMotion]);

  // ---- fly-to ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !props.flyTarget) return;
    const { center, zoom } = props.flyTarget;
    const tilt = props.terrain3d && zoom >= TILT_MIN_ZOOM ? { pitch: TILT_PITCH } : props.terrain3d ? { pitch: 0 } : {};
    if (props.reducedMotion) map.jumpTo({ center, zoom, ...tilt });
    else map.flyTo({ center, zoom, ...tilt, duration: 1600, essential: true });
  }, [props.flyTarget, ready, props.reducedMotion]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- selection pin ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (!props.pin) return;
    const el = document.createElement("div");
    el.style.cssText = "width:14px;height:14px;border-radius:50%;background:#faf9f6;border:3px solid #9e3b2c;box-shadow:0 1px 6px rgba(0,0,0,.5)";
    el.setAttribute("role", "presentation");
    const marker = new maplibregl.Marker({ element: el })
      .setLngLat([props.pin[1], props.pin[0]])
      .addTo(map);
    return () => {
      marker.remove();
    };
  }, [props.pin, ready]);

  // ---- tile outline ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource("tile-outline") as maplibregl.GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: props.tilePolygon ? [ringToPolygon(props.tilePolygon)] : [],
    });
  }, [props.tilePolygon, ready]);

  // ---- cached/featured pins ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const src = map.getSource("pins") as maplibregl.GeoJSONSource | undefined;
    src?.setData({
      type: "FeatureCollection",
      features: props.pins.map((p) => ({
        type: "Feature" as const,
        properties: { tile: p.tile, color: p.tier, label: p.label },
        geometry: { type: "Point" as const, coordinates: [p.lon, p.lat] },
      })),
    });
  }, [props.pins, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setFilter("pin-point", props.selectedTile
      ? ["!", ["all", ["has", "point_count"], ["==", ["get", "tile"], props.selectedTile]]]
      : ["!", ["has", "point_count"]]);
  }, [props.selectedTile, ready]);

  // ---- pair footprint (Data tab selection) ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource("pair-footprint") as maplibregl.GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: props.pairFootprint ? [ringToPolygon(props.pairFootprint)] : [],
    });
  }, [props.pairFootprint, ready]);

  // Pressing "draw this pair's footprint on the globe" also brings it into view: a footprint is a
  // swath hundreds of km wide, so at the pin's zoom it is entirely off-screen. Selecting a bar alone
  // does not move the camera (only this key does).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !props.fitFootprintKey) return;
    // A footprint is a swath hundreds of km wide: at the pin's zoom it is entirely off-screen, so
    // "draw on the globe" must also bring it into view (the pin and tile stay drawn).
    const ring = props.pairFootprint;
    if (ring && ring.length >= 3 && ring.every((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]) && Math.abs(p[1]) <= 90)) {
      const lons = ring.map((p) => p[0]), lats = ring.map((p) => p[1]);
      const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      const wide = typeof window !== "undefined" && window.innerWidth >= 1024;
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], {
        padding: { top: 70, bottom: 70, left: wide ? 360 : 40, right: wide ? 380 : 40 },
        maxZoom: 9, duration: reduce ? 0 : 900,
      });
    }
  }, [props.fitFootprintKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- detection outline ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource("detection-outline") as maplibregl.GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: props.detectionOutline ? [ringToPolygon(props.detectionOutline)] : [],
    });
  }, [props.detectionOutline, ready]);

  // ---- overlays: add/update-in-place/remove (most recently enabled on top) ----
  // S1/U6: bounds crossing the antimeridian are SPLIT at ±180 (maplibre 5.24 cannot draw a
  // seam-crossing image source: the wrapped form stretches the long way round, the unwrapped
  // form breaks tile enumeration — verified in the spike). Each part gets its own crop of the
  // colorized texture. Pure display logic; the rectangle itself always comes from the engine.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    let cancelled = false;
    const wanted = new Map(props.overlays.map((o) => [o.id, o]));

    const applyPart = (id: string, url: string, bounds: [number, number, number, number], opacity: number,
                       corners?: OverlaySpec["corners"]) => {
      const coordinates = corners ?? [
        [bounds[0], bounds[3]], [bounds[2], bounds[3]],
        [bounds[2], bounds[1]], [bounds[0], bounds[1]],
      ];
      const existing = map.getSource(id) as maplibregl.ImageSource | undefined;
      if (!existing) {
        map.addSource(id, { type: "image", url, coordinates });
      } else if (corners) {
        existing.setCoordinates(corners);
      }
      if (!map.getLayer(id)) {
        // NOTE: default linear resampling — nearest renders black on maplibre 5.23/5.24 (S1)
        // below the hillshade (relief shows through) and therefore below the outlines/pins (they stay visible on top)
        map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": opacity } },
                     map.getLayer("hillshade") ? "hillshade" : undefined);
      } else {
        map.setPaintProperty(id, "raster-opacity", opacity);
      }
    };

    void (async () => {
      for (const [id, o] of wanted) {
        if (o.corners) {         // one quad, placed exactly where the engine says; no crop, no seam split
          applyPart(`${id}:0`, o.dataUrl, o.bounds, o.opacity, o.corners);
          const extra = `${id}:1`;
          if (map.getLayer(extra)) map.removeLayer(extra);
          if (map.getSource(extra)) map.removeSource(extra);
          continue;
        }
        const parts = seamSplits(o.bounds);
        if (!parts.length) continue;
        for (let i = 0; i < parts.length; i++) {
          const p = parts[i];
          const url = await cropDataUrl(o.dataUrl, p.crop);
          if (cancelled) return;
          applyPart(`${id}:${i}`, url, p.bounds, o.opacity);
        }
        // drop a stale second part when the overlay stopped crossing the seam
        const stale = `${id}:${parts.length}`;
        if (map.getLayer(stale)) map.removeLayer(stale);
        if (map.getSource(stale)) map.removeSource(stale);
      }
      if (cancelled) return;
      for (const id of Object.keys(map.getStyle().sources)) {
        // source ids are `<spec id>:<part>` and the spec id already starts with "overlay:" and may contain ':'.
        // (Stripping the prefix before this lookup made EVERY overlay look unwanted: each one was added and then
        // removed in the same tick, so the legend filled in and the map stayed empty.)
        const overlayId = overlaySpecIdOfSource(id);
        if (overlayId === null) continue;
        if (!wanted.has(overlayId)) {
          if (map.getLayer(id)) map.removeLayer(id);
          map.removeSource(id);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.overlays, ready]);

  return <div ref={containerRef} className="h-full w-full" aria-label="Earth globe in 3D — SAR results and granule coverage" role="application" />;
}
