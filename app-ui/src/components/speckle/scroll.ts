"use client";

/**
 * Speckle scroll system — one story, one progress.
 *
 * The page opens as a pinned WebGL scene: a 4-screen scroll room carries the
 * Earth chapters (I–III) and ends diving into a cloud veil that closes to
 * darkness. The moment the veil is black, the rest of the story — chapters
 * IV to VI — continues as normal, flowing 2D sections that scroll up over
 * the darkened canvas. A requestAnimationFrame loop damps the raw scroll
 * into `smooth`, so the 3D stage and the typography stay frame-locked
 * without re-rendering React.
 *
 * Nothing in the experience moves on its own — if the visitor stops
 * scrolling, the world holds its breath. The only live input is the
 * pointer (parallax).
 */

import { useEffect, useSyncExternalStore } from "react";

/* ------------------------------------------------------------------ */
/* the story timeline — fractions of the pinned Earth scroll room       */
/* ------------------------------------------------------------------ */

export const STORY_SCREENS = 4;

export const TL = {
  hero: { start: 0.0, end: 0.25 },
  ch2: { start: 0.25, end: 0.58 },
  ch3: { start: 0.58, end: 0.84 },
  /** the cloud veil builds through the descent and holds */
  cloudPass: { start: 0.72, end: 0.9 },
  /** darkness closes over the veil — the handoff to the 2D chapters */
  veilDark: { start: 0.93, end: 0.995 },
} as const;

/* ------------------------------------------------------------------ */
/* asset loading state (tiny external store — no re-render storms)      */
/* ------------------------------------------------------------------ */

const loadState = { progress: 0, ready: false };
const loadListeners = new Set<() => void>();

export function setLoadProgress(progress: number, ready: boolean): void {
  loadState.progress = progress;
  loadState.ready = ready || loadState.ready;
  loadListeners.forEach((l) => l());
}

function subscribeLoad(listener: () => void): () => void {
  loadListeners.add(listener);
  return () => {
    loadListeners.delete(listener);
  };
}

export function useLoadState(): { progress: number; ready: boolean } {
  return useSyncExternalStore(
    subscribeLoad,
    () => loadState,
    () => loadState,
  );
}

/* ------------------------------------------------------------------ */
/* shared story state                                                  */
/* ------------------------------------------------------------------ */

export interface StoryState {
  /** raw target progress through the whole story */
  storyP: number;
  /** damped progress — everything renders from this */
  smooth: number;
  /** pointer position, -1..1 (target) */
  mouseX: number;
  mouseY: number;
  /** damped pointer position — used by the 3D stage */
  mx: number;
  my: number;
  /** 0..1 optical → radar blend; written by the compare slider */
  comparePos: number;
  /** damped compare blend */
  compare: number;
  /** true while the compare beat owns the radar blend */
  compareActive: boolean;
  /** prefers-reduced-motion */
  reduced: boolean;
  /** window scrollY */
  scrollY: number;
  /** true once the canvas is fully covered (footer reached) */
  canvasCovered: boolean;
}

export const storyState: StoryState = {
  storyP: 0,
  smooth: 0,
  mouseX: 0,
  mouseY: 0,
  mx: 0,
  my: 0,
  comparePos: 0,
  compare: 0,
  compareActive: false,
  reduced: false,
  scrollY: 0,
  canvasCovered: false,
};

if (typeof window !== "undefined") {
  // debug handle for verification tooling
  (window as unknown as Record<string, unknown>).__speckle = storyState;
}

type Driver = (smoothP: number) => void;

const drivers = new Set<Driver>();

/** Register a per-frame driver. Returns an unsubscribe function. */
export function addDriver(fn: Driver): () => void {
  drivers.add(fn);
  return () => {
    drivers.delete(fn);
  };
}

/* ------------------------------------------------------------------ */
/* math                                                                */
/* ------------------------------------------------------------------ */

export function clamp(v: number, min = 0, max = 1): number {
  return v < min ? min : v > max ? max : v;
}

export function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = clamp((x - edge0) / (edge1 - edge0 || 1e-6));
  return t * t * (3 - 2 * t);
}

/** linear mix */
export function mix(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** classic fade-in → hold → fade-out envelope for an overlay beat */
export function envelope(
  p: number,
  inStart: number,
  inEnd: number,
  outStart: number,
  outEnd: number,
): number {
  return smoothstep(inStart, inEnd, p) * (1 - smoothstep(outStart, outEnd, p));
}

/** interpolate an array-of-numbers field across keyframes */
export function track(
  keys: { p: number; v: number[] }[],
  p: number,
  out?: number[],
): number[] {
  if (p <= keys[0].p) return keys[0].v;
  const last = keys[keys.length - 1];
  if (p >= last.p) return last.v;
  let i = 0;
  while (i < keys.length - 1 && p > keys[i + 1].p) i++;
  const a = keys[i];
  const b = keys[i + 1];
  const t = smoothstep(a.p, b.p, p);
  const res = out ?? new Array(a.v.length);
  for (let k = 0; k < a.v.length; k++) {
    res[k] = a.v[k] + (b.v[k] - a.v[k]) * t;
  }
  return res;
}

/** single-number variant of `track` */
export function track1(keys: { p: number; v: number }[], p: number): number {
  if (p <= keys[0].p) return keys[0].v;
  const last = keys[keys.length - 1];
  if (p >= last.p) return last.v;
  let i = 0;
  while (i < keys.length - 1 && p > keys[i + 1].p) i++;
  const a = keys[i];
  const b = keys[i + 1];
  return a.v + (b.v - a.v) * smoothstep(a.p, b.p, p);
}

/* ------------------------------------------------------------------ */
/* the master loop                                                     */
/* ------------------------------------------------------------------ */

/**
 * Mount once, near the root of the experience. Owns the pointer listener
 * and the damping loop that produces `smooth`, `mx/my` and `compare`.
 */
export function useStoryLoop(): void {
  useEffect(() => {
    storyState.reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    const onPointer = (e: PointerEvent) => {
      storyState.mouseX = (e.clientX / window.innerWidth) * 2 - 1;
      storyState.mouseY = (e.clientY / window.innerHeight) * 2 - 1;
    };
    window.addEventListener("pointermove", onPointer, { passive: true });

    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      // frame-rate-independent damping, so the feel is identical on a
      // 144 Hz display and a modest laptop
      const dt = Math.min(Math.max((now - last) / 1000, 0.001), 0.1);
      last = now;
      const s = storyState;
      if (s.reduced) {
        s.smooth = s.storyP;
        s.mx = s.mouseX;
        s.my = s.mouseY;
        s.compare = s.comparePos;
      } else {
        const k = 1 - Math.exp(-dt * 4.2);
        s.smooth += (s.storyP - s.smooth) * k;
        if (Math.abs(s.storyP - s.smooth) < 0.0004) s.smooth = s.storyP;
        const km = 1 - Math.exp(-dt * 3.2);
        s.mx += (s.mouseX - s.mx) * km;
        s.my += (s.mouseY - s.my) * km;
        const kc = 1 - Math.exp(-dt * 6.0);
        s.compare += (s.comparePos - s.compare) * kc;
      }
      for (const d of drivers) d(s.smooth);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onPointer);
    };
  }, []);
}
