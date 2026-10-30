"use client";

/** FootprintMiniMap (§7.8) — a flat MapLibre map for the selected stack: every granule
 *  footprint of the stack drawn along the track, the selected one highlighted, the search box
 *  outlined amber. FitBounds on the box. Display-only: rings arrive from the engine. */
import { useEffect, useRef } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const AMBER = "#e6c977";
const CYAN = "#3fb6c9";
const VIOLET = "#b39ce8";

interface Props {
  bbox: [number, number, number, number] | null;
  stackRings: { id: string; ring: [number, number][] | null }[];
  selectedId: string;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

export default function FootprintMiniMap({ bbox, stackRings, selectedId }: Props) {
  const elRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const propsRef = useRef({ bbox, stackRings, selectedId });
  propsRef.current = { bbox, stackRings, selectedId };
  const readyRef = useRef(false);

  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    let map: MlMap;
    try {
      map = new maplibregl.Map({
        container: el,
        style: {
          version: 8,
          sources: {},
          layers: [{ id: "bg", type: "background", paint: { "background-color": "#10141a" } }],
        },
        center: [0, 0],
        zoom: 1,
        attributionControl: false,
        interactive: true,
        canvasContextAttributes: { preserveDrawingBuffer: true },
      });
    } catch {
      return; // WebGL unavailable here: the table above still carries every field
    }
    mapRef.current = map;
    map.on("load", () => {
      // mercator (flat) is the default style projection — the globe page owns the globe
      map.addSource("esri", {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        maxzoom: 23,
        attribution: "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community",
      });
      map.addLayer({ id: "esri", type: "raster", source: "esri", paint: { "raster-opacity": 0.85 } });
      map.addSource("stack", { type: "geojson", data: EMPTY });
      map.addLayer({ id: "stack-fill", type: "fill", source: "stack",
                     paint: { "fill-color": CYAN, "fill-opacity": 0.06 } });
      map.addLayer({ id: "stack-line", type: "line", source: "stack",
                     paint: { "line-color": ["coalesce", ["get", "color"], CYAN], "line-width": 1.2 } });
      map.addSource("box", { type: "geojson", data: EMPTY });
      map.addLayer({ id: "box-line", type: "line", source: "box",
                     paint: { "line-color": AMBER, "line-width": 1.6, "line-dasharray": [3, 2] } });
      readyRef.current = true;
      update();
    });
    const update = () => {
      if (!readyRef.current) return;
      const p = propsRef.current;
      const stackSrc = map.getSource("stack") as maplibregl.GeoJSONSource | undefined;
      stackSrc?.setData({
        type: "FeatureCollection",
        features: p.stackRings
          .filter((r) => r.ring && r.ring.length >= 3)
          .map((r) => ({
            type: "Feature" as const,
            properties: { id: r.id, color: r.id === p.selectedId ? AMBER : undefined },
            geometry: { type: "Polygon" as const, coordinates: [[...r.ring!, r.ring![0]] as GeoJSON.Position[]] },
          })),
      });
      const boxSrc = map.getSource("box") as maplibregl.GeoJSONSource | undefined;
      boxSrc?.setData(p.bbox ? {
        type: "FeatureCollection",
        features: [{
          type: "Feature" as const,
          properties: {},
          geometry: { type: "Polygon" as const, coordinates: [[
            [p.bbox[0], p.bbox[1]], [p.bbox[2], p.bbox[1]],
            [p.bbox[2], p.bbox[3]], [p.bbox[0], p.bbox[3]], [p.bbox[0], p.bbox[1]],
          ]] },
        }],
      } : EMPTY);
      if (p.bbox) {
        map.fitBounds(
          [[p.bbox[0], p.bbox[1]], [p.bbox[2], p.bbox[3]]],
          { padding: 18, duration: 0, maxZoom: 11 },
        );
      }
    };
    const obs = new ResizeObserver(() => {
      map.resize();
      update();
    });
    obs.observe(el);
    return () => {
      obs.disconnect();
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
  }, []);

  // data updates without recreating the map
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const stackSrc = map.getSource("stack") as maplibregl.GeoJSONSource | undefined;
    stackSrc?.setData({
      type: "FeatureCollection",
      features: stackRings
        .filter((r) => r.ring && r.ring.length >= 3)
        .map((r) => ({
          type: "Feature" as const,
          properties: { id: r.id, color: r.id === selectedId ? AMBER : undefined },
          geometry: { type: "Polygon" as const, coordinates: [[...r.ring!, r.ring![0]] as GeoJSON.Position[]] },
        })),
    });
  }, [stackRings, selectedId]);

  return (
    <div
      ref={elRef}
      className="h-44 w-full"
      role="img"
      aria-label={`footprint mini-map: ${stackRings.filter((r) => r.ring).length} footprint(s) of the selected stack; the selected granule is outlined amber, the search box is dashed amber`}
    />
  );
}
