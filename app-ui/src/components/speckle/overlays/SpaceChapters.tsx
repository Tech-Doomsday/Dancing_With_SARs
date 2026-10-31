"use client";

/**
 * The space chapters — fixed overlays for the Earth story (chapters I–III).
 *
 * The hero is a two-subject composition: the typography lives in a column
 * on the left, the Earth holds the right of the frame (the camera track in
 * the stage frames it there and keeps it large). Each line of type rides at
 * a different depth — different scroll-drift and pointer parallax — so the
 * page reads dimensional rather than pasted over a canvas. A quiet orbital
 * readout ties the words to the globe's motion: it only ticks while the
 * visitor is actually scrolling.
 *
 * The story ends inside a cloud veil: clouds build through the descent,
 * hold, and then darkness closes over them — a clean black handoff to the
 * flowing 2D chapters that scroll up over the canvas.
 */

import { useEffect, useRef } from "react";
import {
  addDriver,
  smoothstep,
  clamp,
  storyState,
  track1,
  TL,
} from "../scroll";
import { EARTH_ROTY, EARTH_DIST } from "../earth/choreo";
import { HERO, CHAPTER_2, CHAPTER_3 } from "../data";
import { Overlay } from "./shared";

/* ------------------------------------------------------------------ */
/* the hero — type on the left, the world on the right                  */
/* ------------------------------------------------------------------ */

function Hero() {
  const root = useRef<HTMLDivElement>(null);
  const eyebrow = useRef<HTMLParagraphElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const subtitle = useRef<HTMLParagraphElement>(null);
  const body = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    const layers = [
      [eyebrow, 1.55, 20], // depth, pointer-parallax px
      [title, 1.25, 11],
      [subtitle, 0.95, 6],
      [body, 0.8, 3],
    ] as const;

    return addDriver((p) => {
      const el = root.current;
      if (!el) return;
      const out = 1 - smoothstep(0.107, 0.25, p);
      el.style.opacity = out.toFixed(3);
      el.style.visibility = out <= 0.004 ? "hidden" : "visible";

      const reduce = storyState.reduced;
      const exit = (1 - out) * 56;
      for (const [ref, depth, mouse] of layers) {
        const l = ref.current;
        if (!l) continue;
        const y = -exit * depth;
        const x = reduce ? 0 : storyState.mx * mouse;
        const yy = reduce ? 0 : storyState.my * mouse * 0.55;
        l.style.transform = `translate3d(${x.toFixed(1)}px, ${(y + yy).toFixed(1)}px, 0)`;
      }
    });
  }, []);

  return (
    <div
      ref={root}
      className="absolute inset-0 flex items-center"
      style={{ opacity: 0, visibility: "hidden" }}
    >
      {/* legibility scrim — only where type and world overlap (small screens) */}
      <div
        aria-hidden
        className="absolute inset-0 bg-gradient-to-r from-[#04070d]/85 via-[#04070d]/35 to-transparent lg:hidden"
      />

      <div className="relative mx-auto w-full max-w-7xl px-6 sm:px-10">
        <div className="max-w-lg">
          <p
            ref={eyebrow}
            className="mb-6 text-[10px] font-medium uppercase leading-relaxed tracking-[0.38em] text-[#8b9bb4] will-change-transform sm:text-[11px]"
          >
            {HERO.eyebrow}
          </p>
          <h1
            ref={title}
            className="font-display text-[clamp(3.6rem,9vw,7.5rem)] font-light leading-[0.9] tracking-[0.04em] text-white will-change-transform [text-shadow:0_0_90px_rgba(4,7,13,0.9),0_2px_30px_rgba(4,7,13,0.55)]"
          >
            {HERO.title}
          </h1>
          <p
            ref={subtitle}
            className="mt-6 font-display text-xl italic tracking-wide text-white/90 will-change-transform [text-shadow:0_1px_22px_rgba(4,7,13,0.85)] sm:text-2xl"
          >
            {HERO.subtitle}
          </p>
          <p
            ref={body}
            className="mt-4 max-w-md text-sm leading-relaxed text-[#9fb0c6] will-change-transform [text-shadow:0_1px_16px_rgba(4,7,13,0.9)] sm:text-[15px]"
          >
            {HERO.body}
          </p>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* scroll cue                                                          */
/* ------------------------------------------------------------------ */

function ScrollCue() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    return addDriver((p) => {
      const el = ref.current;
      if (!el) return;
      const o = 1 - smoothstep(0.03, 0.12, p);
      el.style.opacity = o.toFixed(3);
      el.style.visibility = o <= 0.01 ? "hidden" : "visible";
    });
  }, []);
  return (
    <div
      ref={ref}
      className="absolute bottom-8 left-1/2 flex -translate-x-1/2 flex-col items-center gap-3 text-white/60"
      style={{ opacity: 0 }}
    >
      <span className="text-[10px] font-medium uppercase tracking-[0.42em]">
        {HERO.cue}
      </span>
      <span className="relative block h-12 w-px overflow-hidden bg-white/15">
        <span className="cue-line absolute left-0 top-0 h-4 w-px bg-white/80" />
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the orbital readout — altitude + sub-satellite point                */
/* ------------------------------------------------------------------ */

function OrbitalReadout() {
  const ref = useRef<HTMLDivElement>(null);
  const altRef = useRef<HTMLSpanElement>(null);
  const posRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const fmt = (n: number) =>
      n.toLocaleString("en-US", { maximumFractionDigits: 0 });

    return addDriver((p) => {
      const el = ref.current;
      if (!el) return;
      const o = smoothstep(0.01, 0.045, p) * (1 - smoothstep(0.74, 0.81, p));
      el.style.opacity = o.toFixed(3);
      el.style.visibility = o <= 0.01 ? "hidden" : "visible";
      if (o <= 0.01) return;

      // altitude from the camera's distance to the globe's centre
      const dist = track1(EARTH_DIST, clamp(p, 0, 1));
      const altKm = Math.max(0.6, (dist - 2) * 3185.5);
      if (altRef.current) altRef.current.textContent = `ALT ${fmt(altKm)} KM`;

      // sub-satellite point follows the scroll-driven rotation
      const rotY = track1(EARTH_ROTY, clamp(p, 0, 1));
      let lon = 30 + (4.4 - rotY) * 57.29578;
      lon = ((((lon + 180) % 360) + 360) % 360) - 180;
      const hemi = lon >= 0 ? "E" : "W";
      if (posRef.current) {
        posRef.current.textContent = `SUB-POINT 23.4°N ${Math.abs(lon).toFixed(1)}°${hemi}`;
      }
    });
  }, []);

  return (
    <div
      ref={ref}
      aria-hidden
      className="absolute bottom-7 left-6 hidden select-none flex-col gap-1.5 font-mono text-[10px] tracking-[0.22em] text-white/45 sm:flex sm:left-10"
      style={{ opacity: 0 }}
    >
      <span className="text-[9px] uppercase tracking-[0.34em] text-white/30">
        Orbital track
      </span>
      <span ref={altRef}>ALT · KM</span>
      <span ref={posRef}>SUB-POINT ·</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the cloud veil — builds through the descent, then darkness closes   */
/* ------------------------------------------------------------------ */

function CloudPass() {
  const ref = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const dark = useRef<HTMLDivElement>(null);

  useEffect(() => {
    return addDriver((p) => {
      const el = ref.current;
      const im = inner.current;
      if (!el || !im) return;
      // clouds build and HOLD — the story ends inside them
      const o = smoothstep(TL.cloudPass.start, TL.cloudPass.end, p);
      el.style.opacity = o.toFixed(3);
      el.style.visibility = o <= 0.004 ? "hidden" : "visible";
      if (!storyState.reduced) {
        // falling through the layer as the world changes below
        const drift = (1 - smoothstep(TL.cloudPass.start, TL.cloudPass.end, p)) * 12;
        im.style.transform = `translate3d(0, ${drift.toFixed(2)}%, 0)`;
      }
      // darkness closes over the veil — a clean black handoff
      if (dark.current) {
        dark.current.style.opacity = smoothstep(
          TL.veilDark.start,
          TL.veilDark.end,
          p,
        ).toFixed(3);
      }
    });
  }, []);

  return (
    <div
      ref={ref}
      aria-hidden
      className="absolute inset-0 overflow-hidden"
      style={{ opacity: 0, visibility: "hidden" }}
    >
      <div ref={inner} className="absolute inset-0 will-change-transform">
        <div className="absolute inset-0 bg-[#5c6a78]/[0.9]" />
        <div
          className="absolute -inset-[20%] opacity-70"
          style={{
            background:
              "radial-gradient(42% 30% at 30% 40%, rgba(170,188,206,0.75) 0%, rgba(92,106,120,0) 70%), radial-gradient(38% 26% at 72% 62%, rgba(150,170,190,0.7) 0%, rgba(92,106,120,0) 72%), radial-gradient(50% 20% at 50% 12%, rgba(196,210,224,0.8) 0%, rgba(92,106,120,0) 78%)",
          }}
        />
        <div className="absolute inset-0 bg-gradient-to-b from-[#0a1524]/45 via-transparent to-[#574639]/45" />
        {/* the closing dark */}
        <div
          ref={dark}
          className="absolute inset-0 bg-[#04070d]"
          style={{ opacity: 0 }}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* chapter II — the planet begins to move                              */
/* ------------------------------------------------------------------ */

function ChapterTwo() {
  return (
    <Overlay
      range={[0.298, 0.4, 0.481, 0.579]}
      className="absolute inset-0 flex items-center"
    >
      <div className="mx-auto w-full max-w-7xl px-6 sm:px-10">
        <div className="max-w-md ml-auto text-right sm:mr-[8%]">
          <p className="chapter-kicker">{CHAPTER_2.kicker}</p>
          <h2 className="mt-4 font-display text-4xl font-light leading-[1.08] text-white [text-shadow:0_2px_28px_rgba(4,7,13,0.9)] sm:text-5xl">
            {CHAPTER_2.heading}
          </h2>
          <p className="mt-5 text-sm leading-relaxed text-white/75 [text-shadow:0_1px_16px_rgba(4,7,13,0.95)] sm:text-[15px]">
            {CHAPTER_2.body}
          </p>
        </div>
      </div>
    </Overlay>
  );
}

/* ------------------------------------------------------------------ */
/* chapter III — the descent                                           */
/* ------------------------------------------------------------------ */

function ChapterThree() {
  return (
    <Overlay
      range={[0.6, 0.7, 0.757, 0.829]}
      drift={40}
      className="absolute inset-0 flex items-end"
    >
      <div className="mx-auto w-full max-w-7xl px-6 pb-[16vh] sm:px-10">
        <div className="max-w-xl">
          <p className="chapter-kicker">{CHAPTER_3.kicker}</p>
          <h2 className="mt-4 font-display text-4xl font-light leading-[1.08] text-white [text-shadow:0_2px_28px_rgba(4,7,13,0.9)] sm:text-5xl lg:text-6xl">
            {CHAPTER_3.heading}
          </h2>
          <p className="mt-5 max-w-lg text-sm leading-relaxed text-white/80 [text-shadow:0_1px_16px_rgba(4,7,13,0.95)] sm:text-[15px]">
            {CHAPTER_3.body}
          </p>
        </div>
      </div>
    </Overlay>
  );
}

/* ------------------------------------------------------------------ */
/* exported stack                                                      */
/* ------------------------------------------------------------------ */

export default function SpaceChapters() {
  return (
    <div className="pointer-events-none absolute inset-0">
      <div className="absolute inset-0 z-[4]">
        <Hero />
        <ScrollCue />
        <OrbitalReadout />
      </div>
      <div className="absolute inset-0 z-[5]">
        <ChapterTwo />
        <ChapterThree />
      </div>
      <div className="absolute inset-0 z-[3]">
        <CloudPass />
      </div>
    </div>
  );
}
