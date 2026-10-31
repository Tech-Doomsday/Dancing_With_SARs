"use client";

/**
 * The loading veil — a dark, quiet screen with the Speckle wordmark and a
 * thin progress line. It lifts as soon as the Earth has rendered its first
 * frame, so the visitor never sees a broken globe or a flashing texture.
 */

import { useEffect, useState } from "react";
import { useLoadState } from "./scroll";

export default function Loader({
  ready,
  onDone,
}: {
  ready: boolean;
  onDone: () => void;
}) {
  const { progress } = useLoadState();
  const [minTimeDone, setMinTimeDone] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setMinTimeDone(true), 1100);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    if (!ready || !minTimeDone || leaving || gone) return;
    const t = window.setTimeout(() => {
      setLeaving(true);
      window.setTimeout(() => {
        setGone(true);
        onDone();
      }, 900);
    }, 20);
    return () => window.clearTimeout(t);
  }, [ready, minTimeDone, leaving, gone, onDone]);

  if (gone) return null;

  const pct = Math.round(Math.max(progress, ready ? 1 : 0.04) * 100);

  return (
    <div
      role="status"
      aria-label="Loading the Speckle experience"
      className={`fixed inset-0 z-[100] flex flex-col items-center justify-center bg-[#04070d] transition-opacity duration-[900ms] ease-out ${
        leaving ? "pointer-events-none opacity-0" : "opacity-100"
      }`}
    >
      <p className="font-display text-4xl font-light tracking-[0.32em] text-white/90">
        SPECKLE
      </p>
      <p className="mt-3 text-[10px] uppercase tracking-[0.4em] text-[#66788f]">
        Establishing orbit
      </p>
      <div className="mt-8 h-px w-40 overflow-hidden bg-white/10">
        <div
          className="h-full bg-white/70 transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p
        className="mt-3 text-[10px] tabular-nums tracking-[0.3em] text-[#4d5c72]"
        aria-hidden
      >
        {String(pct).padStart(3, "0")}
      </p>
    </div>
  );
}
