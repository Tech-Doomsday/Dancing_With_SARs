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
import "./globe-overrides.css";

const ESRI_ATTRIBUTION = "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community";
const ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export interface OverlaySpec {
  id: string;            // unique per source|bundle|pipeline|layer
  dataUrl: string;
  bounds: [number, number, number, number]; // [W, S, E, N]
  opacity: number;
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
  detectionOutline: [number, number][] | null;
  flat: boolean;
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
    try {
      map = new maplibregl.Map({
        container,
        style: STYLE,
        center: [0, 20],
        zoom: 1.4,
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
      map.addLayer({ id: "pair-footprint-line", type: "line", source: "pair-footprint",
                     paint: { "line-color": "#b08d57", "line-width": 2 } });
      map.addSource("detection-outline", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "detection-line", type: "line", source: "detection-outline",
                     paint: { "line-color": "#9e3b2c", "line-width": 2.5, "line-dasharray": [2, 1.5] } });
      // §7.2 bottom-right: zoom + compass, scale, attribution — with the engine's Esri
      // attribution already bound to the raster source
      map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: false }), "bottom-right");
      map.addControl(new maplibregl.ScaleControl({ maxWidth: 96, unit: "metric" }), "bottom-right");
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
    map.once("load", () => startSpin());
    const userInteracted = () => stopSpin();
    map.on("mousedown", userInteracted);
    map.on("touchstart", userInteracted);
    map.on("wheel", userInteracted);
    map.on("dragstart", userInteracted);
    return () => {
      stopSpin();
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

  // ---- fly-to ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !props.flyTarget) return;
    const { center, zoom } = props.flyTarget;
    if (props.reducedMotion) map.jumpTo({ center, zoom });
    else map.flyTo({ center, zoom, duration: 1600, essential: true });
  }, [props.flyTarget, ready, props.reducedMotion]);

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
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const wanted = new Map(props.overlays.map((o) => [o.id, o]));
    for (const [id, o] of wanted) {
      if (!map.getSource(id)) {
        map.addSource(id, { type: "image", url: o.dataUrl, coordinates: [
          [o.bounds[0], o.bounds[3]], [o.bounds[2], o.bounds[3]],
          [o.bounds[2], o.bounds[1]], [o.bounds[0], o.bounds[1]],
        ] });
      }
      if (!map.getLayer(id)) {
        // NOTE: default linear resampling — nearest renders black on maplibre 5.23/5.24 (S1)
        map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": o.opacity } });
      } else {
        map.setPaintProperty(id, "raster-opacity", o.opacity);
      }
    }
    for (const id of Object.keys(map.getStyle().sources)) {
      if (id.startsWith("overlay:") && !wanted.has(id)) {
        if (map.getLayer(id)) map.removeLayer(id);
        map.removeSource(id);
      }
    }
  }, [props.overlays, ready]);

  return <div ref={containerRef} className="h-full w-full" aria-label="Earth globe — SAR results and granule coverage" role="application" />;
}
