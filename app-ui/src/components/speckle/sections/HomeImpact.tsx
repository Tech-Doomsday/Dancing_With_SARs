"use client";

/**
 * Chapter X — The view from home.
 *
 * Bangladesh, full-frame: a delta under monsoon skies, and the honest
 * reason this project exists here of all places — optical satellites
 * lose this country behind cloud for much of the year, and radar does
 * not. The watchlist is qualitative and public knowledge only; the
 * pipelines named are the real roadmap, each with its status in the
 * open. No results are claimed — only the readiness to watch.
 */

import { useEffect, useRef } from "react";
import Image from "next/image";
import { ArrowRight } from "lucide-react";
import { HOME, THEMES, type HomeWatch } from "../data";
import { cinematify } from "./anim";

function WatchRow({ w }: { w: HomeWatch }) {
  return (
    <div className="grid gap-6 border-t border-white/[0.07] py-11 md:grid-cols-12 md:gap-10">
      <div className="md:col-span-5">
        <h3 className="font-display text-2xl font-light text-white sm:text-3xl">
          {w.region}
        </h3>
        <p className="mt-2.5 font-mono text-[10px] uppercase tracking-[0.26em] text-[var(--accent)]/70">
          {w.tag}
        </p>
        <ul className="mt-5 flex flex-wrap gap-2" aria-label="Pipelines watching this region">
          {w.pipelines.map((p) => (
            <li
              key={p.code}
              className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] py-1.5 pl-3 pr-3.5"
            >
              <span
                className={`block h-1 w-1 rounded-full ${
                  p.live
                    ? "bg-[#8fd8c8] shadow-[0_0_8px_rgba(143,216,200,0.8)]"
                    : "bg-white/20"
                }`}
                aria-hidden
              />
              <span className="font-mono text-[10px] tracking-[0.08em] text-white/45">
                {p.code}
              </span>
              <span className="text-[11px] tracking-tight text-white/70">
                {p.name}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="md:col-span-7">
        <p className="max-w-xl text-sm leading-relaxed text-[#93a3ba] sm:text-[15px]">
          {w.body}
        </p>
      </div>
    </div>
  );
}

export default function HomeImpact() {
  const coverRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (coverRef.current) return cinematify(coverRef.current);
  }, []);
  useEffect(() => {
    if (listRef.current) return cinematify(listRef.current);
  }, []);

  return (
    <section
      id="bangladesh"
      aria-labelledby="bangladesh-heading"
      className="relative bg-[#04070d]"
      style={{ ["--accent" as string]: THEMES.home.accent }}
    >
      {/* the cover — the delta, full frame */}
      <div ref={coverRef} className="relative h-[74svh] min-h-[540px] overflow-hidden">
        <div data-parallax className="absolute -inset-y-[8%] inset-x-0">
          <Image
            src="/landscapes/delta_home.webp"
            alt="Aerial view of a vast braided river delta, sediment-laden channels meeting the sea."
            fill
            sizes="100vw"
            quality={82}
            className="object-cover"
          />
        </div>
        {/* melting out of the impact dark above, into the chapter dark below */}
        <div
          aria-hidden
          className="absolute inset-x-0 top-0 h-[24svh] bg-gradient-to-b from-[#04070d] via-[#04070d]/45 to-transparent"
        />
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-[30svh] bg-gradient-to-t from-[#04070d] via-[#04070d]/55 to-transparent"
        />
        <div
          aria-hidden
          className="absolute inset-0 bg-[#04070d]/25"
        />
        <div aria-hidden className="grain pointer-events-none absolute inset-0" />

        <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-center justify-center px-5 text-center sm:px-8">
          <p data-reveal className="chapter-kicker text-[var(--accent)]">
            {HOME.kicker}
          </p>
          <h2
            data-words
            id="bangladesh-heading"
            className="mt-5 max-w-3xl font-display text-4xl font-light leading-[1.08] text-white [text-shadow:0_2px_34px_rgba(4,7,13,0.8)] sm:text-5xl"
          >
            {HOME.heading}
          </h2>
          <p
            data-reveal
            className="mt-6 max-w-xl text-sm leading-relaxed text-white/78 [text-shadow:0_1px_18px_rgba(4,7,13,0.9)] sm:text-[15px]"
          >
            {HOME.body}
          </p>
        </div>
      </div>

      {/* the home watchlist */}
      <div ref={listRef} className="relative mx-auto max-w-5xl px-5 pb-28 pt-16 sm:px-8 sm:pt-20">
        <p data-reveal className="chapter-kicker text-[var(--accent)]">
          {HOME.watchKicker}
        </p>
        <h3
          data-words
          className="mt-3 font-display text-3xl font-light text-white sm:text-4xl"
        >
          {HOME.watchHeading}
        </h3>

        <div className="mt-14" data-stagger>
          {HOME.watches.map((w) => (
            <WatchRow key={w.region} w={w} />
          ))}
        </div>

        <p data-reveal className="mt-14 max-w-xl text-[12px] leading-relaxed text-white/40">
          {HOME.note}
        </p>

        <div data-reveal className="mt-10">
          <a
            href={HOME.cta.href}
            target="_blank"
            rel="noreferrer noopener"
            className="group inline-flex items-center gap-2.5 rounded-full bg-white px-8 py-4 text-[12px] font-semibold uppercase tracking-[0.2em] text-[#0a1220] transition-all duration-300 hover:bg-[var(--accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)]"
          >
            {HOME.cta.label}
            <ArrowRight
              className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1"
              aria-hidden
            />
          </a>
        </div>
      </div>
    </section>
  );
}
