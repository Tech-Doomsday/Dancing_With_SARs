"use client";

/** Floating search pill (§7.2): explicit Latitude / Longitude numeric fields, a paste-friendly
 *  parser, "Center on my location" (DEC2), and a Globe/Flat toggle. The map is never the only
 *  way to do anything: every action here is keyboard operable. */
import { useEffect, useRef, useState } from "react";
import { Locate, Globe, Map as MapIcon, Search } from "lucide-react";
import { parseCoordinates } from "@/lib/geo-input";
import { currentPosition, geoState, type GeoState } from "@/lib/geolocation";

export interface SearchBarProps {
  lat: string;
  lon: string;
  onLat: (v: string) => void;
  onLon: (v: string) => void;
  onSubmit: (lat: number, lon: number) => void;
  onLocate: (lat: number, lon: number) => void;
  flat: boolean;
  onToggleFlat: () => void;
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
          <input
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
          <input
            value={props.lon}
            onChange={(e) => props.onLon(e.target.value)}
            inputMode="decimal"
            autoComplete="off"
            placeholder="-118.25"
            className="mono mt-0.5 w-full rounded-md border border-[#e7e2da] bg-white px-2 py-1.5 text-sm text-[#1c1a17] outline-none focus:border-[#2c5f58]"
            aria-label="Longitude (-180 to 180)"
          />
        </label>
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
        <button
          type="button"
          onClick={props.onToggleFlat}
          aria-pressed={props.flat}
          className="inline-flex items-center gap-1 rounded-md border border-[#e7e2da] bg-white px-2 py-1 text-[11px] text-[#4a443c] hover:border-[#2c5f58]"
        >
          {props.flat ? <Globe size={13} aria-hidden /> : <MapIcon size={13} aria-hidden />}
          {props.flat ? "Globe" : "Flat"}
        </button>
      </div>
      {geoMsg && <p role="status" className="mt-2 text-[11px] text-[#7a6438]">{geoMsg}</p>}
    </div>
  );
}
