"use client";

/**
 * Chapter VI — What makes Speckle different.
 *
 * Opens as a title card, then pins into one continuous radar pass: a real
 * satellite scan plays as the full-bleed background while the three rules
 * of the engine arrive and leave around the pass, exactly like the terrain
 * words of chapter IV. Closes with the open-science ledger. When motion is
 * reduced, the same beats play as quiet, flowing sections.
 */

import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { ArrowUpRight } from "lucide-react";
import { INNOVATION, THEMES } from "../data";
import { cinematify, prefersReducedMotion } from "./anim";

const PASS_VIDEO = "/media/sar-pass.mp4";
const PASS_POSTER = "/media/sar-pass-poster.jpg";

/* ------------------------------------------------------------------ */
/* the pinned pass — one sweep, three rules                             */
/* ------------------------------------------------------------------ */

function SweepSequence() {
  const root = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const beats = useRef<Array<HTMLDivElement | null>>([]);
  const barFill = useRef<HTMLSpanElement>(null);
  const readout = useRef<HTMLSpanElement>(null);
  const progress = useRef({ p: 0 });
  const [near, setNear] = useState(false);

  /* mount the video only as the sequence approaches the viewport */
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => setNear(e.isIntersecting),
      { rootMargin: "35% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  /* the background scan — plays while the pass is on stage, holds its
     breath the moment the visitor scrolls on (or hides the tab) */
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const vis = () => {
      if (near && !document.hidden) {
        v.play().catch(() => undefined);
      } else {
        v.pause();
      }
    };
    vis();
    document.addEventListener("visibilitychange", vis);
    return () => document.removeEventListener("visibilitychange", vis);
  }, [near]);

  useEffect(() => {
    const b0 = beats.current[0];
    const b1 = beats.current[1];
    const b2 = beats.current[2];
    if (!root.current || !b0 || !b1 || !b2) return;

    const ctx = gsap.context(() => {
      gsap.set([b0, b1, b2], { autoAlpha: 0, y: 44 });

      let active = -1;
      const tl = gsap.timeline({
        defaults: { ease: "none" },
        scrollTrigger: {
          trigger: root.current!,
          start: "top top",
          end: "bottom bottom",
          scrub: 0.7,
          onUpdate: (self) => {
            const p = self.progress;
            progress.current.p = p;
            if (barFill.current)
              barFill.current.style.transform = `scaleX(${p})`;
            if (readout.current)
              readout.current.textContent = `SWATH ${Math.round(p * 100)}%`;
            const i = p < 0.34 ? 0 : p < 0.67 ? 1 : 2;
            if (i === active) return;
            active = i;
            beats.current.forEach((el, k) => {
              if (!el) return;
              el.inert = k !== i;
              el.style.pointerEvents = k === i ? "auto" : "none";
            });
          },
        },
      });

      /* — rule 01, as the pass opens — */
      tl.to(b0, { autoAlpha: 1, y: 0, duration: 0.06, ease: "power2.out" }, 0.05);
      tl.to(b0, { autoAlpha: 0, y: -44, duration: 0.055, ease: "power2.in" }, 0.27);
      /* — rule 02, mid-sweep — */
      tl.to(b1, { autoAlpha: 1, y: 0, duration: 0.06, ease: "power2.out" }, 0.38);
      tl.to(b1, { autoAlpha: 0, y: -44, duration: 0.055, ease: "power2.in" }, 0.6);
      /* — rule 03 stays on stage as the pass ends — */
      tl.to(b2, { autoAlpha: 1, y: 0, duration: 0.06, ease: "power2.out" }, 0.71);
    }, root.current);

    return () => ctx.revert();
  }, []);

  return (
    <div
      ref={root}
      data-frame
      role="region"
      aria-label="One radar pass: the three rules of the engine"
      className="relative z-[1]"
      style={{ height: "352svh" }}
    >
      <div className="sticky top-0 h-svh overflow-hidden bg-[#04070d]">
        {/* the background scan — the real pass, full frame */}
        {near ? (
          <video
            ref={video}
            className="absolute inset-0 h-full w-full object-cover"
            src={PASS_VIDEO}
            poster={PASS_POSTER}
            muted
            loop
            playsInline
            preload="auto"
            aria-hidden
          />
        ) : null}

        {/* the grade — vignette and grain, cut from the same film */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-[6]"
          style={{
            background:
              "radial-gradient(ellipse 120% 90% at 50% 45%, rgba(4,7,13,0) 50%, rgba(4,7,13,0.34) 84%, rgba(4,7,13,0.62) 100%)",
          }}
        />
        <div
          aria-hidden
          className="grain pointer-events-none absolute inset-0 z-[6] opacity-70"
        />
        {/* scrims so the words always read over the scan — edges kept
            quiet on both text sides; the middle stays open for the pass.
            On narrow screens the beats run full-width, so the whole
            scene sits a step further back. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-[6] bg-[#04070d]/40 lg:hidden"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-0 z-[7] w-[44%] bg-gradient-to-r from-[#04070d]/58 via-[#04070d]/20 to-transparent"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 right-0 z-[7] w-[44%] bg-gradient-to-l from-[#04070d]/58 via-[#04070d]/20 to-transparent"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 z-[5] h-[16svh] bg-gradient-to-b from-[#04070d]/88 to-transparent"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 z-[5] h-[15svh] bg-gradient-to-t from-[#04070d]/88 to-transparent"
        />

        {/* the rules — one on stage at a time, beside the pass. The
            container never traps the wheel: it chains to the page. */}
        {INNOVATION.beats.map((b, i) => (
          <div
            key={b.rule}
            ref={(el) => {
              beats.current[i] = el;
            }}
            inert={i !== 0}
            className="absolute inset-0 z-10 overflow-y-auto"
            style={{ pointerEvents: i === 0 ? "auto" : "none" }}
          >
            <div className="flex min-h-full items-center py-8">
              <div
                className={`mx-auto flex w-full max-w-7xl px-5 sm:px-8 ${
                  b.align === "left" ? "" : "justify-end max-lg:justify-start"
                }`}
              >
                <div className="max-w-md">
                  <p className="chapter-kicker text-[var(--accent)]">{b.rule}</p>
                  <h3 className="mt-3 font-display text-3xl font-light leading-[1.08] text-white [text-shadow:0_2px_28px_rgba(4,7,13,0.92)] sm:text-4xl">
                    {b.title}
                  </h3>
                  <p className="mt-4 text-sm leading-relaxed text-white/78 [text-shadow:0_1px_16px_rgba(4,7,13,0.95)] sm:text-[15px]">
                    {b.body}
                  </p>
                </div>
              </div>
            </div>
          </div>
        ))}

        {/* the instrument line — the pass's own progress */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-6 z-20 mx-auto flex max-w-xl flex-col items-center gap-2.5 px-8"
        >
          <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-white/50">
            L-BAND · ONE PASS · <span ref={readout}>SWATH 0%</span>
          </p>
          <span className="block h-px w-full bg-white/12">
            <span
              ref={barFill}
              className="block h-px w-full origin-left bg-[var(--accent)]/80"
              style={{ transform: "scaleX(0)" }}
            />
          </span>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the quiet beats — reduced motion                                      */
/* ------------------------------------------------------------------ */

function QuietBeats() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) return cinematify(ref.current);
  }, []);
  return (
    <div ref={ref} className="relative mx-auto max-w-5xl px-5 sm:px-8">
      {INNOVATION.beats.map((b) => (
        <div key={b.rule} data-reveal className="border-t border-white/[0.07] py-14">
          <div className={b.align === "left" ? "max-w-xl" : "ml-auto max-w-xl"}>
            <p className="chapter-kicker text-[var(--accent)]">{b.rule}</p>
            <h3 className="mt-3 font-display text-3xl font-light leading-[1.08] text-white sm:text-4xl">
              {b.title}
            </h3>
            <p className="mt-4 text-sm leading-relaxed text-[#93a3ba] sm:text-[15px]">
              {b.body}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the chapter                                                          */
/* ------------------------------------------------------------------ */

export default function Innovation() {
  const openerRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLDivElement>(null);
  const [reduced] = useState(() => prefersReducedMotion());
  const live = !reduced;

  useEffect(() => {
    if (openerRef.current) return cinematify(openerRef.current);
  }, []);
  useEffect(() => {
    if (closeRef.current) return cinematify(closeRef.current);
  }, []);

  return (
    <section
      id="innovation"
      aria-labelledby="innovation-heading"
      className="relative bg-[#04070d]"
      style={{ ["--accent" as string]: THEMES.innovation.accent }}
    >
      {/* the seam — melting up out of the evidence dark */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-[26svh] bg-[linear-gradient(to_bottom,#04070d_0svh,rgba(4,7,13,0.55)_12svh,#04070d_26svh)]"
      />
      {/* an ambient wash of the chapter accent, breathing at the top */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[70svh] opacity-[0.06]"
        style={{
          background:
            "radial-gradient(56% 40% at 50% 0%, var(--accent) 0%, transparent 70%)",
        }}
      />
      <div aria-hidden className="grain pointer-events-none absolute inset-0" />

      {/* the opener */}
      <div
        ref={openerRef}
        className="relative mx-auto max-w-3xl px-5 pb-14 pt-32 text-center sm:px-8 sm:pt-40"
      >
        <p data-reveal className="chapter-kicker text-[var(--accent)]">
          {INNOVATION.kicker}
        </p>
        <h2
          data-words
          id="innovation-heading"
          className="mt-5 font-display text-4xl font-light leading-[1.08] text-white sm:text-5xl"
        >
          {INNOVATION.heading}
        </h2>
        <p data-reveal className="mx-auto mt-6 max-w-xl text-sm leading-relaxed text-[#93a3ba]">
          {INNOVATION.body}
        </p>
        {live ? (
          <div data-reveal aria-hidden className="mt-12 flex flex-col items-center gap-3">
            <span className="text-[10px] font-medium uppercase tracking-[0.42em] text-white/55">
              {INNOVATION.cue}
            </span>
            <span className="relative block h-12 w-px overflow-hidden bg-white/15">
              <span className="cue-line absolute left-0 top-0 h-4 w-px bg-white/80" />
            </span>
          </div>
        ) : null}
      </div>

      {live ? <SweepSequence /> : <QuietBeats />}

      {/* the close — the open-science ledger */}
      <div
        ref={closeRef}
        className="relative mx-auto max-w-4xl px-5 pb-36 pt-14 sm:px-8"
      >
        <span
          data-draw
          aria-hidden
          className="block h-px w-24 origin-left bg-[var(--accent)]/50"
        />
        <p data-reveal className="chapter-kicker mt-5 text-[var(--accent)]">
          {INNOVATION.ossKicker}
        </p>
        <h3
          data-words
          className="mt-3 font-display text-3xl font-light text-white sm:text-4xl"
        >
          {INNOVATION.ossHeading}
        </h3>
        <p data-reveal className="mt-4 max-w-2xl text-sm leading-relaxed text-[#93a3ba]">
          {INNOVATION.ossBody}
        </p>
        <ul data-stagger className="mt-8 flex flex-wrap gap-2.5" aria-label="Open-science building blocks">
          {INNOVATION.ossChips.map((c) => (
            <li
              key={c}
              className="rounded-full border border-white/12 bg-white/[0.03] px-4 py-2 font-mono text-[11px] tracking-[0.08em] text-white/70"
            >
              {c}
            </li>
          ))}
        </ul>
        <div data-reveal className="mt-9">
          <a
            href={INNOVATION.ossLink.href}
            target="_blank"
            rel="noreferrer noopener"
            className="group inline-flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.24em] text-white/60 transition-colors hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)]"
          >
            {INNOVATION.ossLink.label}
            <ArrowUpRight
              className="h-3.5 w-3.5 transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
              aria-hidden
            />
          </a>
        </div>
      </div>
    </section>
  );
}
