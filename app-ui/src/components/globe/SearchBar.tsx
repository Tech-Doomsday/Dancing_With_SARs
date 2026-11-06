"use client";

/** Floating search pill (§7.2): explicit Latitude / Longitude numeric fields, a paste-friendly
 *  parser, "Center on my location" (DEC2), and a Globe/Flat toggle. The map is never the only
 *  way to do anything: every action here is keyboard operable. */
import { useEffect, useRef, useState } from "react";
import { Locate, Globe, Map as MapIcon, Mountain, RotateCcw, RotateCw, Search } from "lucide-react";
import { parseCoordinates } from "@/lib/geo-input";
import { currentPosition, geoState, type GeoState } from "@/lib/geolocation";

export interface SearchBarProps {
  lat: string;
  lon: string;
  onLat: (v: string) => void;
  onLon: (v: string) => void;
  /** side length (km) of the square box drawn around the pin; "" = the engine's own tile */
  range?: string;
  onRange?: (v: string) => void;
  /** one honest line about what the range does / why it is not usable */
  rangeNote?: string | null;
  rangeBad?: boolean;
  onSubmit: (lat: number, lon: number) => void;
  onLocate: (lat: number, lon: number) => void;
  flat: boolean;
  onToggleFlat: () => void;
  /** 3D terrain (relief, hillshade, tilt) and its vertical exaggeration; omitted = no 3D controls */
  terrain3d?: boolean;
  onToggleTerrain?: () => void;
  terrainExaggeration?: number;
  onTerrainExaggeration?: (v: number) => void;
  /** extruded OSM buildings and the camera presets of the 3D controls */
  buildings3d?: boolean;
  onToggleBuildings?: () => void;
  onView?: (cmd: { pitch?: number; rotateBy?: number }) => void;
  reducedMotion: boolean;
}

export default function SearchBar(props: SearchBarProps) {
  const latRef = useRef<HTMLInputElement | null>(null);
  const [geo, setGeo] = useState<GeoState>("unavailable");
  const [geoMsg, setGeoMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    geoState(navigator as never).then((s) => alive && setGeo(s));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        latRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = () => {
    const parsed = parseCoordinates(`${props.lat}, ${props.lon}`);
    if (!parsed.ok) {
      setErr(parsed.error);
      return;
    }
    setErr(null);
    props.onSubmit(parsed.value.lat, parsed.value.lon);
  };

  const locate = async () => {
    setBusy(true);
    setGeoMsg(null);
    const state = await geoState(navigator as never);
    if (state === "denied" || state === "unavailable") {
      setBusy(false);
      setGeoMsg(
        state === "denied"
          ? "location permission was denied — you can still type coordinates"
          : "this browser cannot provide your location — you can still type coordinates",
      );
      return;
    }
    const out = await currentPosition(navigator as never);
    setBusy(false);
    if (out.ok) {
      props.onLocate(out.position.lat, out.position.lon);
    } else {
      setGeoMsg(out.message);
    }
  };

  return (
    <div className="pointer-events-auto w-[min(92vw,340px)] rounded-xl border border-[#e7e2da] bg-[#faf9f6]/95 p-3 shadow-lg backdrop-blur">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="flex items-end gap-2"
      >
        <label className="flex-1 text-[11px] font-medium text-[#4a443c]">
          Latitude
          <input suppressHydrationWarning
            ref={latRef}
            value={props.lat}
            onChange={(e) => props.onLat(e.target.value)}
            inputMode="decimal"
            autoComplete="off"
            placeholder="34.05"
            className="mono mt-0.5 w-full rounded-md border border-[#e7e2da] bg-white px-2 py-1.5 text-sm text-[#1c1a17] outline-none focus:border-[#2c5f58]"
            aria-label="Latitude (-90 to 90)"
          />
        </label>
        <label className="flex-1 text-[11px] font-medium text-[#4a443c]">
          Longitude
          <input suppressHydrationWarning
            value={props.lon}
            onChange={(e) => props.onLon(e.target.value)}
            inputMode="decimal"
            autoComplete="off"
            placeholder="-118.25"
            className="mono mt-0.5 w-full rounded-md border border-[#e7e2da] bg-white px-2 py-1.5 text-sm text-[#1c1a17] outline-none focus:border-[#2c5f58]"
            aria-label="Longitude (-180 to 180)"
          />
        </label>
        {props.onRange && (
          <label className="w-[72px] shrink-0 text-[11px] font-medium text-[#4a443c]"
                 title="Side length of the square box around the pin, in km. Empty = the engine's own tile (about 10 km).">
            Range km
            <input suppressHydrationWarning
              value={props.range ?? ""}
              onChange={(e) => props.onRange?.(e.target.value)}
              inputMode="decimal"
              autoComplete="off"
              placeholder="tile"
              className="mono mt-0.5 w-full rounded-md border border-[#e7e2da] bg-white px-2 py-1.5 text-sm text-[#1c1a17] outline-none focus:border-[#2c5f58]"
              aria-label="Box range in km (side length)"
            />
          </label>
        )}
        <button
          type="submit"
          title="Go to coordinates"
          className="flex h-[34px] w-[34px] items-center justify-center rounded-md border border-[#2c5f58] bg-[#2c5f58] text-white hover:bg-[#245049]"
        >
          <Search size={15} aria-hidden />
          <span className="sr-only">Go to coordinates</span>
        </button>
      </form>
      {err && <p role="alert" className="mt-2 text-[11px] text-[#9e3b2c]">{err}</p>}
      {props.rangeNote && (
        <p role={props.rangeBad ? "alert" : undefined}
           className={`mt-2 text-[11px] ${props.rangeBad ? "text-[#9e3b2c]" : "text-[#6b655d]"}`}>{props.rangeNote}</p>
      )}
      <div className="mt-2 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={locate}
          disabled={busy || geo === "unavailable" || geo === "denied"}
          title={geo === "denied" ? "location permission was denied" : undefined}
          className="inline-flex items-center gap-1 rounded-md border border-[#e7e2da] bg-white px-2 py-1 text-[11px] text-[#2c5f58] hover:border-[#2c5f58] disabled:cursor-not-allowed disabled:text-[#8a837b]"
        >
          <Locate size={13} aria-hidden />
          {geo === "granted" ? "Center on my location" : "Center on my location"}
        </button>
        <span className="flex items-center gap-1.5">
        {props.onToggleTerrain && (
          <button
            type="button"
            onClick={props.onToggleTerrain}
            aria-pressed={!!props.terrain3d}
            title="3D terrain: real relief under the imagery; overlays follow it. Right-drag (or Ctrl-drag) to tilt and rotate."
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] ${
              props.terrain3d ? "border-[#1c1a17] bg-[#1c1a17] text-[#faf9f6]" : "border-[#e7e2da] bg-white text-[#4a443c] hover:border-[#2c5f58]"
            }`}
          >
            <Mountain size={13} aria-hidden /> 3D
          </button>
        )}
        <button
          type="button"
          onClick={props.onToggleFlat}
          aria-pressed={props.flat}
          className="inline-flex items-center gap-1 rounded-md border border-[#e7e2da] bg-white px-2 py-1 text-[11px] text-[#4a443c] hover:border-[#2c5f58]"
        >
          {props.flat ? <Globe size={13} aria-hidden /> : <MapIcon size={13} aria-hidden />}
          {props.flat ? "Globe" : "Flat"}
        </button>
        </span>
      </div>
      {props.terrain3d && props.onView && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-[#4a443c]" role="group" aria-label="3D camera">
          <button type="button" onClick={() => props.onView?.({ pitch: 0 })} title="look straight down, north up"
                  className="rounded-md border border-[#e7e2da] bg-white px-2 py-1 hover:border-[#2c5f58]">Top-down</button>
          <button type="button" onClick={() => props.onView?.({ pitch: 55 })} title="tilt the camera (also: right-drag or Ctrl-drag)"
                  className="rounded-md border border-[#e7e2da] bg-white px-2 py-1 hover:border-[#2c5f58]">Tilt</button>
          <button type="button" onClick={() => props.onView?.({ pitch: 72 })} title="low, near-horizon view"
                  className="rounded-md border border-[#e7e2da] bg-white px-2 py-1 hover:border-[#2c5f58]">Low</button>
          <button type="button" onClick={() => props.onView?.({ rotateBy: -30 })} title="rotate 30° counter-clockwise" aria-label="rotate counter-clockwise"
                  className="rounded-md border border-[#e7e2da] bg-white p-1 hover:border-[#2c5f58]"><RotateCcw size={13} aria-hidden /></button>
          <button type="button" onClick={() => props.onView?.({ rotateBy: 30 })} title="rotate 30° clockwise" aria-label="rotate clockwise"
                  className="rounded-md border border-[#e7e2da] bg-white p-1 hover:border-[#2c5f58]"><RotateCw size={13} aria-hidden /></button>
          {props.onToggleBuildings && (
            <label className="ml-auto flex items-center gap-1" title="OpenStreetMap buildings from zoom 13. Many OSM buildings have no height tag and show as 5 m blocks.">
              <input type="checkbox" checked={!!props.buildings3d} onChange={props.onToggleBuildings} /> buildings
            </label>
          )}
        </div>
      )}
      {props.terrain3d && props.onTerrainExaggeration && (
        <label className="mt-2 flex items-center gap-2 text-[11px] text-[#4a443c]" title="Vertical exaggeration of the relief. 1 = true scale; flat deltas need more to be visible.">
          relief ×{(props.terrainExaggeration ?? 1.5).toFixed(1)}
          <input
            type="range" min={1} max={5} step={0.5}
            value={props.terrainExaggeration ?? 1.5}
            onChange={(e) => props.onTerrainExaggeration?.(Number(e.target.value))}
            aria-label="relief exaggeration"
            className="flex-1"
          />
        </label>
      )}
      {geoMsg && <p role="status" className="mt-2 text-[11px] text-[#7a6438]">{geoMsg}</p>}
    </div>
  );
}
