"use client";

/**
 * Chapter VII — How Speckle is built.
 *
 * The engine as one machine you can take apart: five thin, solid layers
 * that sit as a stack and explode apart in 3D as the visitor scrolls,
 * the active stage lighting its edge while its story crossfades in the
 * detail panel below. On narrow screens — or with motion reduced — the
 * same five stages read as a flat, honest ledger. Every layer name,
 * chip and note is real: the repository's own architecture, simplified
 * without being bent.
 */

import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ArrowUpRight } from "lucide-react";
import { ARCHITECTURE, PIPELINES, THEMES, type ArchLayer } from "../data";
import { cinematify } from "./anim";

/* chips + the honest machine-room dots, shared by slab and detail */
function LayerChips({ layer }: { layer: ArchLayer }) {
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      {layer.chips.map((c) => (
        <span
          key={c}
          className="rounded-full border border-white/10 px-2.5 py-1 font-mono text-[9px] uppercase tracking-[0.14em] text-white/45"
        >
          {c}
        </span>
      ))}
      {layer.id === "pipelines" ? (
        <span className="ml-1 flex items-center gap-[7px]" aria-hidden>
          <span className="flex items-center gap-[5px]">
            {PIPELINES.map((p) => (
              <span
                key={p.code}
                className="block h-[5px] w-[5px] rounded-full"
                style={
                  p.live
                    ? {
                        backgroundColor: "var(--accent)",
                        boxShadow: "0 0 7px var(--accent)",
                      }
                    : { backgroundColor: "rgba(255,255,255,0.16)" }
                }
              />
            ))}
          </span>
          <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/35">
            17 · 2 live
          </span>
        </span>
      ) : null}
    </div>
  );
}

/* one stage of the machine */
function Slab({ layer, i }: { layer: ArchLayer; i: number }) {
  return (
    <div
      className="arch-slab"
      style={{ ["--i" as string]: i }}
      aria-labelledby={`arch-${layer.id}-title`}
    >
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 items-baseline gap-3.5">
          <span className="shrink-0 font-mono text-[11px] tracking-[0.18em] text-[var(--accent)]">
            {layer.no}
          </span>
          <h3
            id={`arch-${layer.id}-title`}
            className="truncate font-display text-2xl font-light text-white"
          >
            {layer.title}
          </h3>
        </div>
        <p className="arch-slab-tag w-full text-[10px] font-medium uppercase tracking-[0.2em] text-white/40 sm:w-auto sm:text-right">
          {layer.tagline}
        </p>
      </div>
      {/* the layer's body — shown flat, hidden when exploded (it lives
          in the detail panel then) */}
      <div className="arch-slab-body">
        <p className="mt-3 text-[13px] leading-relaxed text-white/65">
          {layer.body}
        </p>
        <LayerChips layer={layer} />
      </div>
    </div>
  );
}

export default function Architecture() {
  const openerRef = useRef<HTMLDivElement>(null);
  const notesRef = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const sticky = useRef<HTMLDivElement>(null);
  const stack = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (openerRef.current) return cinematify(openerRef.current);
  }, []);
  useEffect(() => {
    if (notesRef.current) return cinematify(notesRef.current);
  }, []);

  /* the explosion — only on wide screens, only with motion allowed.
     The machine comes apart through the first half of the scroll, then
     HOLDS fully exploded for the rest of the pin, so the visitor can
     actually rest on the complete view before the chapter moves on. */
  useEffect(() => {
    if (!root.current || !stack.current || !sticky.current) return;
    const mm = gsap.matchMedia();
    mm.add(
      "(min-width: 1024px) and (prefers-reduced-motion: no-preference)",
      () => {
        const el = stack.current!;
        const holder = sticky.current!;
        gsap.set(el, { rotateX: 58, rotateZ: -14, "--gap": 26, y: 6 });

        let active = -1;
        const tl = gsap.timeline({
          defaults: { ease: "none" },
          scrollTrigger: {
            trigger: root.current!,
            start: "top top",
            end: "bottom bottom",
            scrub: 0.7,
            onUpdate: (self) => {
              /* one story every fifth of the pin — the last two land
                 during the hold, when the machine is fully apart */
              const i = Math.min(4, Math.floor(self.progress / 0.2));
              if (i !== active) {
                active = i;
                holder.dataset.active = String(i);
              }
            },
          },
        });
        tl.fromTo(
          el,
          { autoAlpha: 0.25 },
          { autoAlpha: 1, duration: 0.05 },
          0,
        );
        /* a whisper of drift, so the take-apart feels alive — the
           explosion itself is centred on the stack plane (±2 gaps),
           so almost no compensation is needed to keep it framed */
        tl.fromTo(
          el,
          { y: 6 },
          { y: 16, duration: 0.55 },
          0,
        );
        /* the take-apart itself — every tween below lives in the first
           55% of the timeline; the tail is a hold, so the fully exploded
           machine stays on stage (and readable) for the whole second
           half of the pin instead of completing at the very end */
        tl.to(
          el,
          { rotateX: 45, rotateZ: -3, "--gap": 76, duration: 0.52 },
          0.03,
        );
        /* the hold — pads the timeline to 1.0 so 55% of timeline time
           is 55% of the scroll, and nothing moves after it */
        tl.to({ _: 0 }, { _: 1, duration: 0.45 }, 0.55);
      },
    );
    return () => mm.revert();
  }, []);

  return (
    <section
      id="architecture"
      aria-labelledby="architecture-heading"
      className="relative bg-[#04070d]"
      style={{ ["--accent" as string]: THEMES.architecture.accent }}
    >
      {/* the seam — melting out of the innovation dark */}
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

      {/* the opener */}
      <div
        ref={openerRef}
        className="relative mx-auto max-w-3xl px-5 pb-12 pt-28 text-center sm:px-8 sm:pt-36"
      >
        <p data-reveal className="chapter-kicker text-[var(--accent)]">
          {ARCHITECTURE.kicker}
        </p>
        <h2
          data-words
          id="architecture-heading"
          className="mt-5 font-display text-4xl font-light leading-[1.08] text-white sm:text-5xl"
        >
          {ARCHITECTURE.heading}
        </h2>
        <p data-reveal className="mx-auto mt-6 max-w-xl text-sm leading-relaxed text-[#93a3ba]">
          {ARCHITECTURE.body}
        </p>
        <div data-reveal aria-hidden className="mt-12 flex flex-col items-center gap-3">
          <span className="text-[10px] font-medium uppercase tracking-[0.42em] text-white/55">
            {ARCHITECTURE.cue}
          </span>
          <span className="relative block h-12 w-px overflow-hidden bg-white/15">
            <span className="cue-line absolute left-0 top-0 h-4 w-px bg-white/80" />
          </span>
        </div>
      </div>

      {/* the machine — exploded on scroll, flat when it must be */}
      <div ref={root} className="arch-pin relative z-[1]">
        <div ref={sticky} className="arch-sticky overflow-hidden" data-active="0">
          <p className="arch-caption-flat mx-auto mb-8 max-w-xl px-2 text-center font-mono text-[9px] uppercase tracking-[0.32em] text-white/40">
            {ARCHITECTURE.flowCaption}
          </p>
          <div className="arch-stage">
            <div
              ref={stack}
              className="arch-stack"
              role="list"
              aria-label="The five stages of the engine"
            >
              {ARCHITECTURE.layers.map((l, i) => (
                <Slab key={l.id} layer={l} i={i} />
              ))}
            </div>
          </div>
          {/* the layer stories — crossfading with the active slab */}
          <div className="arch-detail">
            {ARCHITECTURE.layers.map((l) => (
              <div key={l.id} className="arch-detail-item" data-d={Number(l.no) - 1}>
                <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--accent)]">
                  {l.no} · {l.tagline}
                </p>
                <p className="mt-2.5 text-[13px] leading-relaxed text-white/70">
                  {l.body}
                </p>
                <LayerChips layer={l} />
              </div>
            ))}
          </div>
          <p
            aria-hidden
            className="arch-caption text-center font-mono text-[9px] uppercase tracking-[0.32em] text-white/40"
          >
            {ARCHITECTURE.flowCaption}
          </p>
        </div>
      </div>

      {/* the quiet machinery + the real document */}
      <div
        ref={notesRef}
        className="relative mx-auto max-w-6xl px-5 pb-36 pt-16 sm:px-8"
      >
        <p data-reveal className="chapter-kicker text-[var(--accent)]">
          {ARCHITECTURE.sideKicker}
        </p>
        <div className="mt-8 grid gap-10 md:grid-cols-3">
          {ARCHITECTURE.sideNotes.map((n) => (
            <div key={n.title} data-reveal className="relative pt-5">
              <span
                data-draw
                aria-hidden
                className="absolute left-0 top-0 block h-px w-full origin-left bg-gradient-to-r from-[var(--accent)]/50 via-white/10 to-transparent"
              />
              <h3 className="font-display text-xl font-light text-white">
                {n.title}
              </h3>
              <p className="mt-3 text-sm leading-relaxed text-[#93a3ba]">
                {n.body}
              </p>
            </div>
          ))}
        </div>
        <div data-reveal className="mt-14">
          <a
            href={ARCHITECTURE.cta.href}
            target="_blank"
            rel="noreferrer noopener"
            className="group inline-flex items-center gap-2.5 rounded-full border border-white/30 px-7 py-3.5 text-[12px] font-semibold uppercase tracking-[0.2em] text-white/90 transition-all duration-300 hover:border-[var(--accent)] hover:bg-white/[0.05] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)]"
          >
            {ARCHITECTURE.cta.label}
            <ArrowUpRight
              className="h-4 w-4 transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
              aria-hidden
            />
          </a>
        </div>
      </div>
    </section>
  );
}
