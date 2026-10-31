"use client";

/**
 * Chapter IX — What Speckle is built to make possible.
 *
 * A breathing chapter between two heavy ones: three audiences in quiet
 * rows, then the life of an answer drawn as a single line — event,
 * trigger, scan, verdict, response. Framed honestly: these are the
 * engine's design goals, stated with the same candour as its verdicts.
 */

import { useEffect, useRef } from "react";
import gsap from "gsap";
import { IMPACT, THEMES } from "../data";
import { cinematify, prefersReducedMotion } from "./anim";

export default function Impact() {
  const root = useRef<HTMLElement>(null);
  const lifeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (root.current) return cinematify(root.current);
  }, []);

  /* the life-of-an-answer strip — the line draws, the nodes light */
  useEffect(() => {
    if (prefersReducedMotion() || !lifeRef.current) return;
    const ctx = gsap.context(() => {
      const strip = lifeRef.current!;
      const line = strip.querySelector<HTMLElement>("[data-life-line]");
      const dots = strip.querySelectorAll("[data-life-dot]");
      const labels = strip.querySelectorAll("[data-life-label]");

      const tl = gsap.timeline({
        scrollTrigger: { trigger: strip, start: "top 78%", once: true },
      });
      if (line) {
        /* both axes — the line runs horizontally on wide screens and
           vertically on narrow ones; scaling both covers either cut */
        tl.fromTo(
          line,
          { scaleX: 0, scaleY: 0 },
          { scaleX: 1, scaleY: 1, duration: 1.3, ease: "power2.inOut" },
          0,
        );
      }
      tl.fromTo(
        dots,
        { autoAlpha: 0.25 },
        { autoAlpha: 1, duration: 0.4, stagger: 0.16 },
        0.25,
      );
      tl.fromTo(
        labels,
        { autoAlpha: 0, y: 14 },
        { autoAlpha: 1, y: 0, duration: 0.55, ease: "power2.out", stagger: 0.16 },
        0.25,
      );
    }, lifeRef.current);
    return () => ctx.revert();
  }, []);

  return (
    <section
      ref={root}
      id="impact"
      aria-labelledby="impact-heading"
      className="relative bg-[#04070d]"
      style={{ ["--accent" as string]: THEMES.impact.accent }}
    >
      {/* the seam — melting out of the correlations dark */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-[26svh] bg-[linear-gradient(to_bottom,#04070d_0svh,rgba(4,7,13,0.55)_12svh,#04070d_26svh)]"
      />
      <div aria-hidden className="grain pointer-events-none absolute inset-0" />

      <div className="relative mx-auto max-w-5xl px-5 pb-32 pt-32 sm:px-8 sm:pt-40">
        <p data-reveal className="chapter-kicker text-[var(--accent)]">
          {IMPACT.kicker}
        </p>
        <h2
          data-words
          id="impact-heading"
          className="mt-4 max-w-3xl font-display text-4xl font-light leading-[1.08] text-white sm:text-5xl"
        >
          {IMPACT.heading}
        </h2>
        <p data-reveal className="mt-6 max-w-2xl text-sm leading-relaxed text-[#93a3ba]">
          {IMPACT.body}
        </p>

        {/* the three audiences */}
        <div className="mt-20">
          {IMPACT.rows.map((r) => (
            <div
              key={r.n}
              data-reveal
              className="grid gap-5 border-t border-white/[0.07] py-12 md:grid-cols-[110px_1fr] md:gap-8"
            >
              <p
                aria-hidden
                className="font-display text-6xl font-light italic leading-none text-white/[0.07]"
              >
                {r.n}
              </p>
              <div>
                <p className="chapter-kicker text-[var(--accent)]">{r.who}</p>
                <h3 className="mt-3 font-display text-2xl font-light leading-snug text-white sm:text-3xl">
                  {r.title}
                </h3>
                <p className="mt-4 max-w-2xl text-sm leading-relaxed text-[#93a3ba]">
                  {r.body}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* the life of an answer */}
        <div ref={lifeRef} className="mt-24">
          <p data-reveal className="chapter-kicker text-[var(--accent)]">
            {IMPACT.lifecycleKicker}
          </p>
          <div className="relative mt-10">
            <ol className="relative flex flex-col gap-10 sm:flex-row sm:justify-between sm:gap-6">
              {/* the spine — horizontal wide, vertical narrow */}
              <span
                aria-hidden
                className="absolute left-[5px] top-[6px] h-[calc(100%-12px)] w-px bg-white/10 sm:left-[7px] sm:top-[5px] sm:h-px sm:w-[calc(100%-14px)]"
              >
                <span
                  data-life-line
                  className="absolute left-0 top-0 h-full w-full origin-top bg-[var(--accent)]/45 sm:origin-left"
                />
              </span>
              {IMPACT.lifecycle.map((s) => (
                <li key={s.label} className="relative flex gap-5 sm:block sm:pr-2">
                  <span
                    data-life-dot
                    className="relative z-[1] mt-1 block h-[11px] w-[11px] shrink-0 rounded-full border border-[var(--accent)] bg-[#04070d] sm:mt-0"
                  />
                  <div data-life-label className="sm:mt-4 sm:max-w-[170px]">
                    <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/80">
                      {s.label}
                    </p>
                    <p className="mt-1.5 text-[12px] leading-relaxed text-white/50">
                      {s.note}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>

        <p data-reveal className="mt-20 max-w-xl text-[12px] leading-relaxed text-white/40">
          {IMPACT.footnote}
        </p>
      </div>
    </section>
  );
}
