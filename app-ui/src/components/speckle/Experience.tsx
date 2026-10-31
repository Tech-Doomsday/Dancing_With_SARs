"use client";

/**
 * Speckle — the landing experience.
 *
 * Two movements, one page. The story opens as a pinned WebGL scene: four
 * screens of scroll room carry the Earth chapters — the still globe, the
 * turn, the descent — and end diving into a cloud veil that closes to
 * black. At that exact moment the rest of the story (chapters IV–VI)
 * arrives as flowing 2D sections that scroll up over the darkened canvas:
 * the changing systems, the evidence chapter, the finale, the footer.
 *
 * A fully static, chapter-themed page is rendered when WebGL is
 * unavailable, and all motion is quieted under prefers-reduced-motion.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import dynamic from "next/dynamic";
import {
  storyState,
  clamp,
  smoothstep,
  useStoryLoop,
  useLoadState,
  addDriver,
  STORY_SCREENS,
} from "./scroll";
import Loader from "./Loader";
import Nav from "./Nav";
import SpaceChapters from "./overlays/SpaceChapters";
import ScrollChapters from "./ScrollChapters";
import Footer from "./Footer";
import StaticStory from "./StaticStory";
import StoryTranscript from "./StoryTranscript";
import { THEME_TRACK, THEMES } from "./data";

/* three.js stays out of the initial bundle */
const Stage = dynamic(() => import("./earth/Stage"), {
  ssr: false,
  loading: () => null,
});

function webglAvailable(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(
      window.WebGLRenderingContext &&
      (c.getContext("webgl2") || c.getContext("webgl"))
    );
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* chapter theme — the accent colour follows the Earth story            */
/* ------------------------------------------------------------------ */

function useChapterTheme(rootRef: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const parsed = THEME_TRACK.map((t) => {
      const m = THEMES[t.key].accent.match(/#(..)(..)(..)/);
      const hex = (s: string) => parseInt(s, 16);
      return { ...t, rgb: [hex(m![1]), hex(m![2]), hex(m![3])] };
    });

    let last = "";
    return addDriver((p) => {
      const el = rootRef.current;
      if (!el) return;
      let i = 0;
      for (let k = 0; k < parsed.length; k++) {
        if (p >= parsed[k].start - 0.008) i = k;
      }
      const seg = parsed[i];
      const prev = parsed[Math.max(0, i - 1)];
      const t = i === 0 ? 1 : smoothstep(seg.start - 0.008, seg.start + 0.012, p);
      const r = Math.round(prev.rgb[0] + (seg.rgb[0] - prev.rgb[0]) * t);
      const g = Math.round(prev.rgb[1] + (seg.rgb[1] - prev.rgb[1]) * t);
      const b = Math.round(prev.rgb[2] + (seg.rgb[2] - prev.rgb[2]) * t);
      const next = `rgb(${r}, ${g}, ${b})`;
      if (next !== last) {
        last = next;
        el.style.setProperty("--accent", next);
      }
    });
  }, [rootRef]);
}

/* ------------------------------------------------------------------ */
/* the experience                                                      */
/* ------------------------------------------------------------------ */

export default function Experience() {
  const [mode, setMode] = useState<"pending" | "webgl" | "static">("pending");
  const [paused, setPaused] = useState(false);
  const [entered, setEntered] = useState(false);
  const [earthReady, setEarthReady] = useState(false);
  const [covered, setCovered] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { ready: assetsReady } = useLoadState();
  const earthOnReady = useCallback(() => setEarthReady(true), []);

  useStoryLoop();
  useChapterTheme(rootRef);

  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      setMode(webglAvailable() ? "webgl" : "static");
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  /* scroll → story progress + canvas visibility */
  useEffect(() => {
    if (mode !== "webgl") return;

    let ticking = false;
    const update = () => {
      ticking = false;
      const spacer = document.getElementById("speckle-story-spacer");
      const storyH = spacer?.offsetHeight || window.innerHeight * STORY_SCREENS;
      storyState.scrollY = window.scrollY;
      storyState.storyP = clamp(window.scrollY / storyH);

      // the fixed stage retires once the 2D chapters have scrolled over it
      const coveredNow =
        spacer != null &&
        window.scrollY >= spacer.offsetTop + spacer.offsetHeight - 2;
      if (coveredNow !== storyState.canvasCovered) {
        storyState.canvasCovered = coveredNow;
        setCovered(coveredNow);
        setPaused(coveredNow);
      }
    };
    const onScroll = () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(update);
      }
    };

    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [mode]);

  /* ---------------------------------------------------------------- */

  if (mode === "pending") {
    return <div className="fixed inset-0 z-[100] bg-[#04070d]" aria-hidden />;
  }

  if (mode === "static") {
    return (
      <div
        id="top"
        ref={rootRef}
        className="relative flex min-h-screen flex-col bg-[#04070d]"
        style={{ ["--accent" as string]: THEMES.space.accent }}
      >
        <Nav />
        <StaticStory />
        <Footer />
      </div>
    );
  }

  return (
    <div
      id="top"
      ref={rootRef}
      className="relative min-h-screen bg-[#04070d]"
      style={{ ["--accent" as string]: THEMES.space.accent }}
    >
      <Loader
        ready={assetsReady && earthReady}
        onDone={() => setEntered(true)}
      />

      {/* the fixed stage — the Earth chapters happen here */}
      <div
        className={`fixed inset-0 z-0 transition-[opacity,visibility] duration-700 ${
          paused ? "invisible opacity-0" : "visible opacity-100"
        }`}
        aria-hidden={paused}
      >
        <Stage onReady={earthOnReady} paused={paused} />
        {/* cinematic finish — a whisper of vignette and film grain over
            the render, never over the typography */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-[1]"
          style={{
            background:
              "radial-gradient(ellipse 120% 90% at 50% 45%, rgba(4,7,13,0) 55%, rgba(4,7,13,0.28) 82%, rgba(4,7,13,0.5) 100%)",
          }}
        />
        <div aria-hidden className="grain pointer-events-none absolute inset-0 z-[1] opacity-60" />
      </div>

      {/* fixed overlays — the Earth chapters' typography */}
      <div
        className={`pointer-events-none fixed inset-0 z-[2] transition-opacity duration-500 ${
          covered ? "opacity-0" : "opacity-100"
        }`}
        aria-hidden={covered}
      >
        {entered ? <SpaceChapters /> : null}
      </div>

      <Nav />

      {/* the Earth chapters in reading order, for assistive technology.
          chapters IV–VI are real DOM below, so they read naturally. */}
      <StoryTranscript />

      {/* the scroll room — 4 screens of Earth story, then the flowing
          2D chapters scroll up over the darkened canvas */}
      <main className="pointer-events-none relative z-10">
        <div
          id="speckle-story-spacer"
          aria-hidden
          className="h-[400vh]"
        />
        <div className="pointer-events-auto relative z-10 bg-[#04070d]">
          <ScrollChapters />
          <Footer />
        </div>
      </main>
    </div>
  );
}
