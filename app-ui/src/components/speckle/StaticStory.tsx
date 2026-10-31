"use client";

/**
 * The static story — rendered when WebGL is unavailable.
 *
 * The same six chapters as quiet, flowing sections, graded with the same
 * film grammar as the main experience: word-mask title cards, dip-to-black
 * cuts between the terrain systems, gradient seams instead of borders,
 * and a drifting Ken Burns on the finale. No interaction is lost: the
 * compare concept becomes a static pair, every CTA remains real.
 */

import { useEffect, useRef } from "react";
import Image from "next/image";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import {
  HERO,
  CHAPTER_2,
  CHAPTER_3,
  CHAPTER_4_INTRO,
  SECTORS,
  EVIDENCE,
  VERDICT_TIERS,
  PIPELINES,
  FINALE,
  THEMES,
  type Sector,
} from "./data";
import { cinematify } from "./sections/anim";
import Innovation from "./sections/Innovation";
import Architecture from "./sections/Architecture";
import Correlations from "./sections/Correlations";
import Impact from "./sections/Impact";
import HomeImpact from "./sections/HomeImpact";

function StatusTag({ status }: { status: Sector["status"] }) {
  return status === "live" ? (
    <span className="text-[10px] font-medium uppercase tracking-[0.24em] text-[#8fd8c8]">
      Live module
    </span>
  ) : (
    <span className="text-[10px] font-medium uppercase tracking-[0.24em] text-[#c8b98d]">
      In development
    </span>
  );
}

function SectorBlock({ sector, first }: { sector: Sector; first: boolean }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (ref.current) return cinematify(ref.current);
  }, []);
  const left = sector.align === "left";

  return (
    <section
      ref={ref}
      aria-labelledby={`${sector.id}-heading`}
      className="relative"
      style={{ ["--accent" as string]: sector.theme.accent }}
    >
      <div className="relative h-[92svh] min-h-[560px] overflow-hidden">
        <div data-parallax className="absolute -inset-y-[8%] inset-x-0">
          <Image
            src={sector.image}
            alt={sector.imageAlt}
            fill
            sizes="100vw"
            quality={78}
            className="object-cover"
            priority={first}
          />
        </div>
        <div
          className={`absolute inset-0 ${
            left
              ? "bg-gradient-to-r from-[#04070d]/90 via-[#04070d]/35 to-transparent"
              : "bg-gradient-to-l from-[#04070d]/90 via-[#04070d]/35 to-transparent"
          }`}
        />
        {/* dip-to-black cuts — each terrain fades in from and out to the
            dark, the quiet cousin of the main experience's dissolves */}
        <div
          aria-hidden
          className="absolute inset-x-0 top-0 h-[14svh] bg-gradient-to-b from-[#04070d] to-transparent"
        />
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-[16svh] bg-gradient-to-t from-[#04070d] to-transparent"
        />
        <div className="absolute inset-0 grain" aria-hidden />
        <div className="relative z-10 mx-auto flex h-full max-w-7xl items-center px-5 sm:px-8">
          <div className={`max-w-xl ${left ? "mr-auto" : "ml-auto"}`}>
            <p data-reveal className="chapter-kicker text-[var(--accent)]">
              System {sector.index} · {sector.kicker}
            </p>
            <h2
              data-reveal
              id={`${sector.id}-heading`}
              className="mt-4 font-display text-4xl font-light leading-[1.06] text-white sm:text-5xl lg:text-6xl"
            >
              {sector.heading}
            </h2>
            <p data-reveal className="mt-5 max-w-md text-sm leading-relaxed text-white/75 sm:text-[15px]">
              {sector.body}
            </p>
            <ul data-reveal className="mt-7 space-y-2" aria-label="Module facts">
              {sector.meta.map((m) => (
                <li
                  key={m}
                  className="flex items-center gap-2.5 text-[11px] uppercase tracking-[0.18em] text-white/55"
                >
                  <span className="block h-px w-4 bg-[var(--accent)]/60" aria-hidden />
                  {m}
                </li>
              ))}
            </ul>
            <div data-reveal className="mt-9 flex flex-wrap items-center gap-4">
              <a
                href={sector.cta.href}
                target="_blank"
                rel="noreferrer noopener"
                className={`group inline-flex items-center gap-2.5 rounded-full px-6 py-3.5 text-[12px] font-semibold uppercase tracking-[0.2em] transition-all duration-300 ${
                  sector.status === "live"
                    ? "bg-white text-[#0a1220] hover:bg-[var(--accent)]"
                    : "border border-white/35 text-white hover:border-[var(--accent)] hover:bg-white/[0.06]"
                }`}
              >
                {sector.cta.label}
                {sector.status === "live" ? (
                  <ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1" aria-hidden />
                ) : (
                  <ArrowUpRight className="h-4 w-4 transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
                )}
              </a>
              <StatusTag status={sector.status} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default function StaticStory() {
  const headRef = useRef<HTMLDivElement>(null);
  const ch4Ref = useRef<HTMLDivElement>(null);
  const evidenceRef = useRef<HTMLElement>(null);
  const finaleRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (headRef.current) return cinematify(headRef.current);
  }, []);
  useEffect(() => {
    if (ch4Ref.current) return cinematify(ch4Ref.current);
  }, []);
  useEffect(() => {
    if (evidenceRef.current) return cinematify(evidenceRef.current);
  }, []);
  useEffect(() => {
    if (finaleRef.current) return cinematify(finaleRef.current);
  }, []);

  return (
    <main className="relative">
      {/* chapter I — hero */}
      <section className="relative flex min-h-[92svh] items-center justify-center overflow-hidden bg-[#04070d]">
        <Image
          src="/fallback/hero-earth.jpg"
          alt=""
          aria-hidden
          fill
          priority
          className="object-cover object-[center_78%]"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-[#04070d]/40 via-transparent to-[#04070d]" />
        <div className="relative z-10 flex flex-col items-center px-6 text-center">
          <p className="mb-6 text-[10px] font-medium uppercase tracking-[0.38em] text-[#8b9bb4] sm:text-[11px]">
            {HERO.eyebrow}
          </p>
          <h1 className="font-display text-[19vw] font-light leading-[0.9] tracking-[0.06em] text-white sm:text-[15vw] lg:text-[10.5rem]">
            {HERO.title}
          </h1>
          <p className="mt-6 font-display text-xl italic tracking-wide text-white/85 sm:text-2xl">
            {HERO.subtitle}
          </p>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-[#93a3ba] sm:text-[15px]">
            {HERO.body}
          </p>
        </div>
      </section>

      {/* chapters II & III */}
      <div ref={headRef} className="relative bg-[#04070d] px-6 py-28 text-center sm:py-36">
        <div className="mx-auto max-w-2xl space-y-24">
          <div data-reveal>
            <p className="chapter-kicker">{CHAPTER_2.kicker}</p>
            <h2 data-words className="mt-4 font-display text-4xl font-light text-white">
              {CHAPTER_2.heading}
            </h2>
            <p className="mx-auto mt-5 max-w-xl text-sm leading-relaxed text-[#93a3ba]">
              {CHAPTER_2.body}
            </p>
          </div>
          <div data-reveal>
            <p className="chapter-kicker">{CHAPTER_3.kicker}</p>
            <h2 data-words className="mt-4 font-display text-4xl font-light text-white">
              {CHAPTER_3.heading}
            </h2>
            <p className="mx-auto mt-5 max-w-xl text-sm leading-relaxed text-[#93a3ba]">
              {CHAPTER_3.body}
            </p>
          </div>
        </div>
      </div>

      {/* chapter IV — systems */}
      <div ref={ch4Ref} className="relative bg-[#04070d]">
        <div className="relative mx-auto max-w-3xl px-5 pb-10 pt-24 text-center sm:px-8">
          <p data-reveal className="chapter-kicker text-[var(--accent)]">
            {CHAPTER_4_INTRO.kicker}
          </p>
          <h2 data-words className="mt-4 font-display text-4xl font-light leading-[1.08] text-white sm:text-5xl">
            {CHAPTER_4_INTRO.heading}
          </h2>
          <p data-reveal className="mx-auto mt-5 max-w-xl text-sm leading-relaxed text-[#93a3ba]">
            {CHAPTER_4_INTRO.body}
          </p>
          <div data-reveal aria-hidden className="mt-12 flex flex-col items-center gap-3">
            <span className="text-[10px] font-medium uppercase tracking-[0.42em] text-white/55">
              {CHAPTER_4_INTRO.cue}
            </span>
            <span className="relative block h-12 w-px overflow-hidden bg-white/15">
              <span className="cue-line absolute left-0 top-0 h-4 w-px bg-white/80" />
            </span>
          </div>
        </div>
        {SECTORS.map((s, i) => (
          <SectorBlock key={s.id} sector={s} first={i === 0} />
        ))}
      </div>

      {/* chapter V — evidence, melting up over the last terrain */}
      <section
        ref={evidenceRef}
        aria-labelledby="evidence-heading"
        className="relative z-10 -mt-[24svh]"
        style={{ ["--accent" as string]: THEMES.evidence.accent }}
      >
        <div
          aria-hidden
          className="absolute inset-0 bg-[linear-gradient(to_bottom,rgba(4,7,13,0)_0svh,rgba(4,7,13,0.62)_14svh,#04070d_24svh)]"
        />
        <div aria-hidden className="grain pointer-events-none absolute inset-0" />
        <div className="relative z-[1] mx-auto max-w-6xl px-5 pb-28 pt-[calc(24svh+6rem)] sm:px-8">
          <span
            data-draw
            aria-hidden
            className="block h-px w-24 origin-left bg-[var(--accent)]/50"
          />
          <p data-reveal className="chapter-kicker mt-5 text-[var(--accent)]">
            {EVIDENCE.kicker}
          </p>
          <h2
            data-words
            id="evidence-heading"
            className="mt-4 max-w-3xl font-display text-4xl font-light leading-[1.08] text-white sm:text-5xl"
          >
            {EVIDENCE.heading}
          </h2>
          <p data-reveal className="mt-6 max-w-2xl text-sm leading-relaxed text-[#93a3ba]">
            {EVIDENCE.body}
          </p>

          <div className="mt-16 grid gap-10 lg:grid-cols-3">
            {EVIDENCE.stages.map((st) => (
              <div key={st.n} data-reveal className="relative pt-6">
                <span
                  data-draw
                  aria-hidden
                  className="absolute left-0 top-0 block h-px w-full origin-left bg-gradient-to-r from-[var(--accent)]/55 via-white/10 to-transparent"
                />
                <p className="stage-number">{st.n}</p>
                <h3 className="stage-title">{st.title}</h3>
                <p className="stage-body">{st.body}</p>
              </div>
            ))}
          </div>

          <div data-reveal className="mt-16 rounded-md border border-white/10 bg-white/[0.03] p-6 sm:p-8">
            <p className="text-[10px] font-medium uppercase tracking-[0.28em] text-white/45">
              Verdict vocabulary, as reported by the engine
            </p>
            <ul className="mt-5 grid gap-3.5 sm:grid-cols-2">
              {VERDICT_TIERS.map((t) => (
                <li key={t.label} className="flex items-start gap-3.5">
                  <span className="mt-1.5 block h-1.5 w-1.5 shrink-0 rounded-full bg-white/25" aria-hidden />
                  <div>
                    <p className="text-sm font-medium text-white/85">{t.label}</p>
                    <p className="mt-0.5 text-xs leading-relaxed text-white/45">{t.note}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div data-reveal className="mt-16">
            <p className="chapter-kicker text-[var(--accent)]">
              {EVIDENCE.engineKicker}
            </p>
            <h3
              data-words
              className="mt-3 font-display text-3xl font-light text-white sm:text-4xl"
            >
              {EVIDENCE.engineHeading}
            </h3>
            <ul
              data-stagger
              className="mt-8 grid grid-cols-1 gap-x-10 sm:grid-cols-2 lg:grid-cols-3"
              aria-label="Engine pipelines"
            >
              {PIPELINES.map((p) => (
                <li
                  key={p.code}
                  className="flex items-baseline gap-3 border-b border-white/[0.06] py-2"
                >
                  <span className={`shrink-0 font-mono text-[10px] tracking-[0.12em] ${p.live ? "text-[var(--accent)]" : "text-white/30"}`}>
                    {p.code}
                  </span>
                  <span className={`truncate text-[12px] ${p.live ? "text-white" : "text-white/45"}`}>
                    {p.name}
                  </span>
                  <span className={`ml-auto text-[8px] font-medium uppercase tracking-[0.2em] ${p.live ? "text-[#8fd8c8]" : "text-white/25"}`}>
                    {p.live ? "live" : "planned"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* chapters VI–X — the same flowing sections as the main experience
          (they are DOM-only, so the static story reuses them verbatim) */}
      <Innovation />
      <Architecture />
      <Correlations />
      <Impact />
      <HomeImpact />

      {/* chapter XI — finale */}
      <section
        ref={finaleRef}
        aria-labelledby="finale-heading"
        className="relative overflow-hidden bg-[#04070d]"
        style={{ ["--accent" as string]: THEMES.finale.accent }}
      >
        <div
          data-kenburns="0.16"
          aria-hidden
          className="absolute -inset-y-[6%] inset-x-0 opacity-50"
          style={{
            backgroundImage: "url(/landscapes/descent.webp)",
            backgroundSize: "cover",
            backgroundPosition: "center 62%",
          }}
        />
        <div
          aria-hidden
          className="absolute inset-x-0 top-0 h-[26svh] bg-gradient-to-b from-[#04070d] to-transparent"
        />
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-[26svh] bg-gradient-to-t from-[#04070d] to-transparent"
        />
        <div aria-hidden className="grain pointer-events-none absolute inset-0" />
        <div className="relative z-10 mx-auto flex min-h-[88svh] max-w-3xl flex-col items-center justify-center px-5 py-24 text-center sm:px-8">
          <p data-reveal className="chapter-kicker">
            {FINALE.kicker}
          </p>
          <h2
            data-words
            id="finale-heading"
            className="mt-5 font-display text-5xl font-light leading-[1.05] text-white sm:text-6xl"
          >
            {FINALE.heading}
          </h2>
          <p data-reveal className="mx-auto mt-6 max-w-xl text-sm leading-relaxed text-[#a5b3c7] sm:text-base">
            {FINALE.body}
          </p>
          <div data-reveal className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
            <a
              href={FINALE.primary.href}
              target="_blank"
              rel="noreferrer noopener"
              className="group inline-flex items-center gap-2.5 rounded-full bg-white px-8 py-4 text-[12px] font-semibold uppercase tracking-[0.2em] text-[#0a1220] transition-all duration-300 hover:bg-[var(--accent)]"
            >
              {FINALE.primary.label}
              <ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1" aria-hidden />
            </a>
            <a
              href={FINALE.secondary.href}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-2.5 rounded-full border border-white/30 px-8 py-4 text-[12px] font-semibold uppercase tracking-[0.2em] text-white/90 transition-all duration-300 hover:border-[var(--accent)] hover:bg-white/[0.05]"
            >
              {FINALE.secondary.label}
            </a>
          </div>
        </div>
      </section>
    </main>
  );
}
