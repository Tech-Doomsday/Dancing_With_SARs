"use client";

/**
 * Chapter VIII — From results to reasons.
 *
 * The chapter that shows we understand the correlations between what the
 * engine measures and what actually happens: six cause → signature →
 * consequence chains, each with its editorial imagery, its honest
 * pipelines and — where the engine really does it — the verdict-stage
 * link note. The chains draw themselves as they enter; the imagery wipes
 * in like a cut from the compare chapter.
 */

import { useEffect, useRef } from "react";
import Image from "next/image";
import gsap from "gsap";
import { ChevronRight } from "lucide-react";
import { CORRELATIONS, THEMES, type Correlation } from "../data";
import { cinematify, prefersReducedMotion } from "./anim";

/* ------------------------------------------------------------------ */
/* one chain node + its connector                                       */
/* ------------------------------------------------------------------ */

function ChainNode({
  label,
  text,
  last,
}: {
  label: string;
  text: string;
  last: boolean;
}) {
  return (
    <>
      <li className="chain-node relative rounded-md border border-white/[0.08] bg-white/[0.02] px-3.5 py-3 sm:flex-1">
        <p className="font-mono text-[9px] uppercase tracking-[0.24em] text-[var(--card-accent)]">
          {label}
        </p>
        <p className="mt-1.5 text-[12px] leading-snug text-white/70">{text}</p>
      </li>
      {!last ? (
        <span
          aria-hidden
          className="flex shrink-0 items-center max-sm:mx-auto max-sm:my-1 max-sm:rotate-90 sm:mx-1 sm:w-6"
        >
          <span
            data-chain-line
            className="block h-px flex-1 origin-left"
            style={{ backgroundColor: "color-mix(in srgb, var(--card-accent) 42%, transparent)" }}
          />
          <ChevronRight
            data-chain-chev
            className="h-3 w-3 shrink-0"
            style={{ color: "color-mix(in srgb, var(--card-accent) 60%, transparent)" }}
          />
        </span>
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* one case file                                                        */
/* ------------------------------------------------------------------ */

function CorrCard({ c, flip }: { c: Correlation; flip: boolean }) {
  return (
    <article
      className="corr-card relative"
      style={{
        ["--accent" as string]: c.accent,
        ["--card-accent" as string]: c.accent,
      }}
      aria-labelledby={`${c.id}-heading`}
    >
      <div className="grid items-center gap-8 lg:grid-cols-12 lg:gap-12">
        <div className={`lg:col-span-5 ${flip ? "lg:order-2" : ""}`}>
          <div className="corr-media relative aspect-[16/10] overflow-hidden rounded-md border border-white/12 shadow-[0_26px_70px_rgba(0,0,0,0.45)]">
            <Image
              src={c.image}
              alt={c.imageAlt}
              fill
              sizes="(min-width: 1024px) 40vw, 92vw"
              quality={78}
              className="object-cover"
            />
            <span className="absolute left-3 top-3 rounded-sm bg-[#04070d]/78 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-white/80 backdrop-blur-sm">
              {c.index}
            </span>
          </div>
        </div>

        <div className="lg:col-span-7">
          <p data-reveal className="chapter-kicker text-[var(--accent)]">
            {c.domain}
          </p>
          <h3
            data-words
            id={`${c.id}-heading`}
            className="mt-3 font-display text-3xl font-light leading-[1.1] text-white sm:text-4xl"
          >
            {c.title}
          </h3>

          <ol
            className="mt-7 flex flex-col sm:flex-row sm:items-stretch"
            aria-label={`Chain: ${c.domain}`}
          >
            {c.chain.map((n, i) => (
              <ChainNode
                key={n.label}
                label={n.label}
                text={n.text}
                last={i === c.chain.length - 1}
              />
            ))}
          </ol>

          <p data-reveal className="mt-6 max-w-xl text-sm leading-relaxed text-[#93a3ba]">
            {c.body}
          </p>

          {c.note ? (
            <p data-reveal className="mt-4 max-w-xl text-[12px] leading-relaxed text-white/45">
              <span className="text-[var(--accent)]" aria-hidden>
                ↳{" "}
              </span>
              {c.note}
            </p>
          ) : null}

          <ul
            data-stagger
            className="mt-6 flex flex-wrap gap-2"
            aria-label="Pipelines that watch this chain"
          >
            {c.pipelines.map((p) => (
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
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ */
/* the chapter                                                          */
/* ------------------------------------------------------------------ */

export default function Correlations() {
  const root = useRef<HTMLElement>(null);

  /* the film grammar for the whole chapter */
  useEffect(() => {
    if (root.current) return cinematify(root.current);
  }, []);

  /* the chains — nodes settle, connectors draw, imagery wipes in */
  useEffect(() => {
    if (prefersReducedMotion() || !root.current) return;
    const ctx = gsap.context(() => {
      root.current!.querySelectorAll<HTMLElement>(".corr-card").forEach((card) => {
        const media = card.querySelector<HTMLElement>(".corr-media");
        const nodes = card.querySelectorAll(".chain-node");
        const lines = card.querySelectorAll("[data-chain-line]");
        const chevs = card.querySelectorAll("[data-chain-chev]");

        const tl = gsap.timeline({
          scrollTrigger: { trigger: card, start: "top 80%", once: true },
        });
        if (media) {
          tl.fromTo(
            media,
            { clipPath: "inset(0 0 100% 0)" },
            { clipPath: "inset(0 0 0% 0)", duration: 1.05, ease: "power3.inOut" },
            0,
          );
        }
        tl.fromTo(
          nodes,
          { autoAlpha: 0, y: 16 },
          { autoAlpha: 1, y: 0, duration: 0.55, ease: "power2.out", stagger: 0.14 },
          0.15,
        );
        tl.fromTo(
          lines,
          { scaleX: 0 },
          { scaleX: 1, duration: 0.5, ease: "power2.inOut", stagger: 0.14 },
          0.32,
        );
        tl.fromTo(
          chevs,
          { autoAlpha: 0 },
          { autoAlpha: 1, duration: 0.3, stagger: 0.14 },
          0.62,
        );
      });
    }, root.current);
    return () => ctx.revert();
  }, []);

  return (
    <section
      ref={root}
      id="correlations"
      aria-labelledby="correlations-heading"
      className="relative bg-[#04070d]"
      style={{ ["--accent" as string]: THEMES.correlation.accent }}
    >
      {/* the seam — melting out of the architecture dark */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-[26svh] bg-[linear-gradient(to_bottom,#04070d_0svh,rgba(4,7,13,0.55)_12svh,#04070d_26svh)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[70svh] opacity-[0.06]"
        style={{
          background:
            "radial-gradient(56% 40% at 50% 0%, var(--accent) 0%, transparent 70%)",
        }}
      />
      <div aria-hidden className="grain pointer-events-none absolute inset-0" />

      <div className="relative mx-auto max-w-6xl px-5 pb-36 pt-32 sm:px-8 sm:pt-40">
        <span
          data-draw
          aria-hidden
          className="block h-px w-24 origin-left bg-[var(--accent)]/50"
        />
        <p data-reveal className="chapter-kicker mt-5 text-[var(--accent)]">
          {CORRELATIONS.kicker}
        </p>
        <h2
          data-words
          id="correlations-heading"
          className="mt-4 max-w-3xl font-display text-4xl font-light leading-[1.08] text-white sm:text-5xl"
        >
          {CORRELATIONS.heading}
        </h2>
        <p data-reveal className="mt-6 max-w-2xl text-sm leading-relaxed text-[#93a3ba]">
          {CORRELATIONS.body}
        </p>

        <div className="mt-24 space-y-24 sm:mt-28 sm:space-y-32">
          {CORRELATIONS.chains.map((c, i) => (
            <CorrCard key={c.id} c={c} flip={i % 2 === 1} />
          ))}
        </div>
      </div>
    </section>
  );
}
