"use client";

/**
 * The flowing chapters — IV to VI, after the Earth story.
 *
 * Where the Earth dive ends in black, the story keeps rolling as one
 * continuous cut. Chapter IV opens as a title card; the three terrain
 * systems then play as a pinned, crossfading sequence — a film dissolve
 * between terrains, a slow Ken Burns drift throughout, the words arriving
 * and leaving around each dissolve. The evidence chapter melts up over
 * the last terrain through a long gradient seam (no border, no cut), the
 * comparison wipes in with a single radar sweep, and the finale racks
 * into focus like a closing title card. Every part blends into the next.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import Image from "next/image";
import gsap from "gsap";
import {
  ArrowRight,
  ArrowUpRight,
  CircleDot,
  MoveHorizontal,
} from "lucide-react";
import {
  CHAPTER_4_INTRO,
  SECTORS,
  EVIDENCE,
  VERDICT_TIERS,
  PIPELINES,
  FINALE,
  THEMES,
  type Sector,
} from "./data";
import { cinematify, prefersReducedMotion } from "./sections/anim";
import Innovation from "./sections/Innovation";
import Architecture from "./sections/Architecture";
import Correlations from "./sections/Correlations";
import Impact from "./sections/Impact";
import HomeImpact from "./sections/HomeImpact";
import { clamp } from "./scroll";

/* ------------------------------------------------------------------ */
/* status tag                                                          */
/* ------------------------------------------------------------------ */

function StatusTag({ status }: { status: Sector["status"] }) {
  if (status === "live") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.24em] text-[#8fd8c8]">
        <CircleDot className="h-3 w-3" aria-hidden />
        Live module
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.24em] text-[#c8b98d]">
      <span
        className="block h-1.5 w-1.5 rounded-full border border-[#c8b98d]/70"
        aria-hidden
      />
      In development
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* a system's words — shared by the sequence and the quiet fallback     */
/* ------------------------------------------------------------------ */

function SystemText({ sector }: { sector: Sector }) {
  const left = sector.align === "left";
  return (
    <div className={`max-w-xl ${left ? "mr-auto" : "ml-auto"}`}>
      <p className="chapter-kicker text-[var(--accent)]">
        System {sector.index} · {sector.kicker}
      </p>
      <h2
        id={`${sector.id}-heading`}
        className="mt-4 font-display text-4xl font-light leading-[1.06] text-white [text-shadow:0_2px_30px_rgba(4,7,13,0.85)] sm:text-5xl lg:text-6xl"
      >
        {sector.heading}
      </h2>
      <p className="mt-5 max-w-md text-sm leading-relaxed text-white/75 [text-shadow:0_1px_18px_rgba(4,7,13,0.9)] sm:text-[15px]">
        {sector.body}
      </p>
      <ul className="mt-7 space-y-2" aria-label="Module facts">
        {sector.meta.map((m) => (
          <li
            key={m}
            className="flex items-center gap-2.5 text-[11px] uppercase tracking-[0.18em] text-white/55"
          >
            <span
              className="block h-px w-4 bg-[var(--accent)]/60"
              aria-hidden
            />
            {m}
          </li>
        ))}
      </ul>
      <div className="mt-9 flex flex-wrap items-center gap-4">
        <a
          href={sector.cta.href}
          target="_blank"
          rel="noreferrer noopener"
          className={`group inline-flex items-center gap-2.5 rounded-full px-6 py-3.5 text-[12px] font-semibold uppercase tracking-[0.2em] transition-all duration-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)] ${
            sector.status === "live"
              ? "bg-white text-[#0a1220] hover:bg-[var(--accent)]"
              : "border border-white/35 text-white hover:border-[var(--accent)] hover:bg-white/[0.06]"
          }`}
        >
          {sector.cta.label}
          {sector.status === "live" ? (
            <ArrowRight
              className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1"
              aria-hidden
            />
          ) : (
            <ArrowUpRight
              className="h-4 w-4 transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
              aria-hidden
            />
          )}
        </a>
        <StatusTag status={sector.status} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the terrains — one pinned sequence, film dissolves between them      */
/* ------------------------------------------------------------------ */

/** The quiet, non-pinned block used when motion is reduced. */
function StaticSector({ sector, first }: { sector: Sector; first: boolean }) {
  const left = sector.align === "left";
  return (
    <section
      aria-labelledby={`${sector.id}-heading`}
      className="relative"
      style={{ ["--accent" as string]: sector.theme.accent }}
    >
      <div className="relative h-[92svh] min-h-[560px] overflow-hidden">
        <Image
          src={sector.image}
          alt={sector.imageAlt}
          fill
          sizes="100vw"
          quality={78}
          priority={first}
          className="object-cover"
        />
        <div
          aria-hidden
          className={`absolute inset-0 ${
            left
              ? "bg-gradient-to-r from-[#04070d]/92 via-[#04070d]/38 to-transparent"
              : "bg-gradient-to-l from-[#04070d]/92 via-[#04070d]/38 to-transparent"
          }`}
        />
        <div
          aria-hidden
          className="absolute inset-0 bg-gradient-to-t from-[#04070d]/60 via-transparent to-[#04070d]/40"
        />
        <div className="relative z-10 mx-auto flex h-full max-w-7xl items-center px-5 sm:px-8">
          <SystemText sector={sector} />
        </div>
      </div>
    </section>
  );
}

function SystemsSequence() {
  const root = useRef<HTMLDivElement>(null);
  const sticky = useRef<HTMLDivElement>(null);
  const layers = useRef<Array<HTMLDivElement | null>>([]);
  const texts = useRef<Array<HTMLDivElement | null>>([]);
  const ticks = useRef<Array<HTMLSpanElement | null>>([]);
  /* client-only mount (behind the experience's WebGL gate) — the media
     query is safe to read at first render */
  const [reduced] = useState(() => prefersReducedMotion());

  useEffect(() => {
    if (reduced || !root.current || !sticky.current) return;
    const l0 = layers.current[0];
    const l1 = layers.current[1];
    const l2 = layers.current[2];
    const t0 = texts.current[0];
    const t1 = texts.current[1];
    const t2 = texts.current[2];
    if (!l0 || !l1 || !l2 || !t0 || !t1 || !t2) return;

    const ctx = gsap.context(() => {
      /* the two dissolves sit at these beats of the pinned travel */
      const B1 = 0.335;
      const B2 = 0.67;

      /* initial states — everything waits in the dark */
      gsap.set([l1, l2], { autoAlpha: 0 });
      gsap.set([t0, t1, t2], { autoAlpha: 0, y: 48 });
      gsap.set(l0, { scale: 1.16, autoAlpha: 0 });
      gsap.set(l1, { scale: 1.18, xPercent: -2.5 });
      gsap.set(l2, { scale: 1.16, xPercent: 2 });

      /* entry dissolve — the first terrain rises out of the chapter dark
         while the title card above scrolls away */
      gsap.to(l0, {
        autoAlpha: 1,
        ease: "none",
        scrollTrigger: {
          trigger: root.current!,
          start: "top bottom",
          end: "top top",
          scrub: 0.5,
        },
      });

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
            const i = p < 0.34 ? 0 : p < 0.675 ? 1 : 2;
            if (i === active) return;
            active = i;
            const accent = SECTORS[i].theme.accent;
            sticky.current!.style.setProperty("--accent", accent);
            texts.current.forEach((el, k) => {
              if (!el) return;
              el.inert = k !== i;
              el.style.pointerEvents = k === i ? "auto" : "none";
            });
            ticks.current.forEach((el, k) => {
              if (!el) return;
              el.style.width = k === i ? "26px" : "14px";
              el.style.backgroundColor =
                k === i ? accent : "rgba(255,255,255,0.28)";
            });
          },
        },
      });

      /* — terrain 01 · the fault line — */
      tl.to(l0, { scale: 1.03, duration: 0.385 }, 0);
      tl.to(t0, { autoAlpha: 1, y: 0, duration: 0.075, ease: "power2.out" }, 0.02);
      tl.to(t0, { autoAlpha: 0, y: -48, duration: 0.07, ease: "power2.in" }, 0.235);

      /* — dissolve — */
      tl.to(l1, { autoAlpha: 1, duration: 0.1 }, B1 - 0.055);
      tl.to(l1, { scale: 1.045, xPercent: 0, duration: 0.37 }, 0.3);
      tl.set(l0, { visibility: "hidden" }, 0.4);

      /* — terrain 02 · the delta — */
      tl.to(t1, { autoAlpha: 1, y: 0, duration: 0.075, ease: "power2.out" }, 0.385);
      tl.to(t1, { autoAlpha: 0, y: -48, duration: 0.07, ease: "power2.in" }, 0.575);

      /* — dissolve — */
      tl.to(l2, { autoAlpha: 1, duration: 0.1 }, B2 - 0.055);
      tl.to(l2, { scale: 1.035, xPercent: 0, duration: 0.355 }, 0.645);
      tl.set(l1, { visibility: "hidden" }, 0.73);

      /* — terrain 03 · the river — stays on stage; the next chapter
           melts up over it through its own gradient seam */
      tl.to(t2, { autoAlpha: 1, y: 0, duration: 0.075, ease: "power2.out" }, 0.72);
    }, root.current);

    return () => ctx.revert();
  }, [reduced]);

  if (reduced) {
    return (
      <div className="relative">
        {SECTORS.map((s, i) => (
          <StaticSector key={s.id} sector={s} first={i === 0} />
        ))}
      </div>
    );
  }

  return (
    <div
      ref={root}
      id="systems"
      data-frame
      role="region"
      aria-label="The changing systems"
      className="relative z-[1]"
      style={{ height: "620svh" }}
    >
      <div
        ref={sticky}
        className="sticky top-0 h-svh overflow-hidden bg-[#04070d]"
        style={{ ["--accent" as string]: SECTORS[0].theme.accent }}
      >
        {/* the terrains — stacked, dissolved one into the next */}
        {SECTORS.map((s, i) => (
          <div
            key={s.id}
            ref={(el) => {
              layers.current[i] = el;
            }}
            className="absolute inset-0 will-change-transform"
            style={{ zIndex: 10 + i * 10 }}
          >
            <Image
              src={s.image}
              alt={s.imageAlt}
              fill
              sizes="100vw"
              quality={82}
              priority={i === 0}
              loading="eager"
              className="object-cover"
            />
            <div
              aria-hidden
              className={`absolute inset-0 ${
                s.align === "left"
                  ? "bg-gradient-to-r from-[#04070d]/92 via-[#04070d]/38 to-transparent"
                  : "bg-gradient-to-l from-[#04070d]/92 via-[#04070d]/38 to-transparent"
              }`}
            />
            <div
              aria-hidden
              className="absolute inset-0 bg-gradient-to-t from-[#04070d]/60 via-transparent to-[#04070d]/40"
            />
          </div>
        ))}

        {/* the grade — the same vignette and grain finish as the Earth stage,
            so the 2D chapters feel cut from the same film */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-40"
          style={{
            background:
              "radial-gradient(ellipse 120% 90% at 50% 45%, rgba(4,7,13,0) 55%, rgba(4,7,13,0.3) 84%, rgba(4,7,13,0.55) 100%)",
          }}
        />
        <div
          aria-hidden
          className="grain pointer-events-none absolute inset-0 z-40 opacity-70"
        />

        {/* the words — one subject per terrain. Only the system on stage
            takes pointer events and focus. */}
        {SECTORS.map((s, i) => (
          <div
            key={s.id}
            ref={(el) => {
              texts.current[i] = el;
            }}
            inert={i !== 0}
            className="absolute inset-0 z-50 overflow-y-auto"
            style={{ pointerEvents: i === 0 ? "auto" : "none" }}
          >
            <div className="flex min-h-full items-center py-8">
              <div className="mx-auto w-full max-w-7xl px-5 sm:px-8">
                <SystemText sector={s} />
              </div>
            </div>
          </div>
        ))}

        {/* the sequence rail — three quiet ticks */}
        <div
          aria-hidden
          className="absolute right-2.5 top-1/2 z-50 flex -translate-y-1/2 flex-col items-end gap-2.5 sm:right-7"
        >
          {SECTORS.map((s, i) => (
            <span
              key={s.id}
              ref={(el) => {
                ticks.current[i] = el;
              }}
              className="block h-px transition-all duration-500"
              style={{
                width: i === 0 ? 26 : 14,
                backgroundColor:
                  i === 0 ? s.theme.accent : "rgba(255,255,255,0.28)",
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the compare slider — optical vs radar, draggable                     */
/* ------------------------------------------------------------------ */

function CompareSlider() {
  const root = useRef<HTMLDivElement>(null);
  const clip = useRef<HTMLDivElement>(null);
  const divider = useRef<HTMLDivElement>(null);
  const readout = useRef<HTMLSpanElement>(null);
  const posRef = useRef(35);
  const dragging = useRef(false);

  const apply = useCallback((v: number) => {
    posRef.current = v;
    if (divider.current) divider.current.style.left = `${v}%`;
    if (clip.current) clip.current.style.clipPath = `inset(0 0 0 ${v}%)`;
    if (readout.current)
      readout.current.textContent = `RADAR ${Math.round(v)}%`;
    if (root.current)
      root.current.setAttribute("aria-valuenow", String(Math.round(v)));
  }, []);

  /* the radar intro — the frame wipes in, then the divider makes one
     scanning pass across the scene before settling, the way the engine
     itself sweeps a scene */
  useEffect(() => {
    if (prefersReducedMotion()) return;
    const el = root.current;
    if (!el) return;
    const ctx = gsap.context(() => {
      gsap.fromTo(
        el,
        { clipPath: "inset(0 100% 0 0)" },
        {
          clipPath: "inset(0 0% 0 0)",
          duration: 1.4,
          ease: "power3.inOut",
          scrollTrigger: { trigger: el, start: "top 80%", once: true },
          onComplete: () => {
            const o = { v: 97 };
            gsap.to(o, {
              v: 35,
              duration: 2.1,
              ease: "power2.inOut",
              onUpdate: () => apply(o.v),
            });
          },
        },
      );
    });
    return () => ctx.revert();
  }, [apply]);

  const setFromClientX = useCallback(
    (clientX: number) => {
      const el = root.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      apply(clamp(((clientX - r.left) / r.width) * 100, 2, 98));
    },
    [apply],
  );

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    setFromClientX(e.clientX);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (dragging.current) setFromClientX(e.clientX);
  };
  const endDrag = () => {
    dragging.current = false;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 10 : 4;
    if (e.key === "ArrowLeft") {
      apply(clamp(posRef.current - step, 2, 98));
      e.preventDefault();
    } else if (e.key === "ArrowRight") {
      apply(clamp(posRef.current + step, 2, 98));
      e.preventDefault();
    } else if (e.key === "Home") {
      apply(2);
      e.preventDefault();
    } else if (e.key === "End") {
      apply(98);
      e.preventDefault();
    }
  };

  return (
    <div>
      <div
        ref={root}
        role="slider"
        tabIndex={0}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={35}
        aria-label="Comparison between optical and radar imagery"
        aria-valuetext="35% radar"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
        className="relative aspect-[16/9] w-full cursor-ew-resize touch-pan-y select-none overflow-hidden rounded-md border border-white/15 bg-[#0a0f18] shadow-[0_30px_90px_rgba(0,0,0,0.5)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)]"
      >
        {/* optical (base) */}
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: "url(/landscapes/earthquake.webp)" }}
          aria-hidden
        />
        {/* radar (clipped) */}
        <div
          ref={clip}
          className="absolute inset-0 bg-cover bg-center"
          style={{
            backgroundImage: "url(/landscapes/sar.webp)",
            clipPath: "inset(0 0 0 35%)",
          }}
          aria-hidden
        />

        <span className="pointer-events-none absolute left-3 top-3 rounded-sm bg-[#04070d]/78 px-2.5 py-1.5 text-[10px] font-medium uppercase tracking-[0.2em] text-white/80 backdrop-blur-sm">
          {EVIDENCE.compareLabels.left}
        </span>
        <span className="pointer-events-none absolute right-3 top-3 rounded-sm bg-[#04070d]/78 px-2.5 py-1.5 text-[10px] font-medium uppercase tracking-[0.2em] text-white/80 backdrop-blur-sm">
          {EVIDENCE.compareLabels.right}
        </span>

        <div
          ref={divider}
          className="pointer-events-none absolute inset-y-0 w-px bg-white/90"
          style={{ left: "35%" }}
        >
          <span className="absolute left-1/2 top-1/2 flex h-10 w-10 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-white/75 bg-[#04070d]/75 text-white backdrop-blur-sm">
            <MoveHorizontal className="h-4 w-4" aria-hidden />
          </span>
        </div>

        <span className="pointer-events-none absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-3 rounded-sm bg-[#04070d]/78 px-3 py-1.5 text-[10px] uppercase tracking-[0.22em] text-white/70 backdrop-blur-sm">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent)] opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
          </span>
          <span ref={readout}>RADAR 35%</span>
          <span className="text-white/35">· {EVIDENCE.compareLabels.hint}</span>
        </span>
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-white/40">
        {EVIDENCE.footnote}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* verdict vocabulary                                                  */
/* ------------------------------------------------------------------ */

const VERDICT_TONE: Record<string, string> = {
  ok: "bg-[#8fd8c8] shadow-[0_0_10px_rgba(143,216,200,0.7)]",
  mid: "bg-[#e3cf9a]",
  off: "bg-[#c98d7e]",
  dim: "bg-white/20",
};

function VerdictList() {
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.03] p-6 sm:p-8">
      <p className="text-[10px] font-medium uppercase tracking-[0.28em] text-white/45">
        Verdict vocabulary, as reported by the engine
      </p>
      <ul className="mt-5 grid gap-3.5 sm:grid-cols-2" aria-label="Possible verdict tiers">
        {VERDICT_TIERS.map((t) => (
          <li key={t.label} className="flex items-start gap-3.5">
            <span
              className={`mt-1.5 block h-1.5 w-1.5 shrink-0 rounded-full ${VERDICT_TONE[t.tone]}`}
              aria-hidden
            />
            <div>
              <p className="text-sm font-medium text-white/85">{t.label}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-white/45">
                {t.note}
              </p>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-6 border-t border-white/[0.07] pt-4 text-xs leading-relaxed text-white/40">
        No calibrated probabilities until labelled histories exist. That part
        is deliberate.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the flowing chapters — one continuous cut                           */
/* ------------------------------------------------------------------ */

export default function ScrollChapters() {
  const introRef = useRef<HTMLDivElement>(null);
  const evidenceRef = useRef<HTMLElement>(null);
  const finaleRef = useRef<HTMLElement>(null);
  const finaleKicker = useRef<HTMLParagraphElement>(null);
  const finaleHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (introRef.current) return cinematify(introRef.current);
  }, []);
  useEffect(() => {
    if (evidenceRef.current) return cinematify(evidenceRef.current);
  }, []);
  useEffect(() => {
    if (finaleRef.current) return cinematify(finaleRef.current);
  }, []);

  /* the closing title card — a rack focus that resolves as it centres */
  useEffect(() => {
    if (prefersReducedMotion() || !finaleRef.current || !finaleHeading.current)
      return;
    const ctx = gsap.context(() => {
      gsap.fromTo(
        finaleHeading.current,
        { opacity: 0.2, scale: 1.08, filter: "blur(18px)" },
        {
          opacity: 1,
          scale: 1,
          filter: "blur(0px)",
          ease: "none",
          scrollTrigger: {
            trigger: finaleRef.current,
            start: "top 88%",
            end: "top 30%",
            scrub: 0.6,
          },
        },
      );
      if (finaleKicker.current) {
        gsap.fromTo(
          finaleKicker.current,
          { autoAlpha: 0, y: 18 },
          {
            autoAlpha: 1,
            y: 0,
            ease: "none",
            scrollTrigger: {
              trigger: finaleRef.current,
              start: "top 86%",
              end: "top 46%",
              scrub: 0.6,
            },
          },
        );
      }
    });
    return () => ctx.revert();
  }, []);

  return (
    <div className="relative bg-[#04070d]">
      {/* chapter IV — the title card after the dark */}
      <div ref={introRef} className="relative bg-[#04070d]">
        <div className="relative mx-auto max-w-3xl px-5 pb-[13svh] pt-28 text-center sm:px-8 sm:pt-36">
          <p data-reveal className="chapter-kicker text-[var(--accent)]">
            {CHAPTER_4_INTRO.kicker}
          </p>
          <h2
            data-words
            className="mt-5 font-display text-4xl font-light leading-[1.08] text-white sm:text-5xl"
          >
            {CHAPTER_4_INTRO.heading}
          </h2>
          <p
            data-reveal
            className="mx-auto mt-6 max-w-xl text-sm leading-relaxed text-[#93a3ba]"
          >
            {CHAPTER_4_INTRO.body}
          </p>
          <div
            data-reveal
            aria-hidden
            className="mt-14 flex flex-col items-center gap-3"
          >
            <span className="text-[10px] font-medium uppercase tracking-[0.42em] text-white/55">
              {CHAPTER_4_INTRO.cue}
            </span>
            <span className="relative block h-12 w-px overflow-hidden bg-white/15">
              <span className="cue-line absolute left-0 top-0 h-4 w-px bg-white/80" />
            </span>
          </div>
        </div>
      </div>

      {/* chapter IV — the terrains, playing as one crossfading sequence */}
      <SystemsSequence />

      {/* chapter V — melts up over the last terrain, no seam, no cut */}
      <section
        ref={evidenceRef}
        id="method"
        aria-labelledby="evidence-heading"
        className="relative z-[3] -mt-[38svh]"
        style={{ ["--accent" as string]: THEMES.evidence.accent }}
      >
        {/* the dissolve — the river terrain fades into the evidence dark */}
        <div
          aria-hidden
          className="absolute inset-0 bg-[linear-gradient(to_bottom,rgba(4,7,13,0)_0svh,rgba(4,7,13,0.62)_22svh,#04070d_38svh)]"
        />
        {/* an ambient wash of the chapter accent, breathing at the top */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-[85svh] opacity-[0.07]"
          style={{
            background:
              "radial-gradient(58% 42% at 50% 0%, var(--accent) 0%, transparent 72%)",
          }}
        />
        <div aria-hidden className="grain pointer-events-none absolute inset-0" />

        <div className="relative z-[1] mx-auto max-w-6xl px-5 pb-28 pt-[calc(38svh+7.5rem)] sm:px-8 sm:pb-36">
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
          <p
            data-reveal
            className="mt-6 max-w-2xl text-sm leading-relaxed text-[#93a3ba]"
          >
            {EVIDENCE.body}
          </p>

          <div className="mt-20 grid gap-12 lg:grid-cols-3">
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

          <div data-reveal className="mx-auto mt-20 max-w-4xl">
            <CompareSlider />
          </div>

          <div data-reveal className="mt-20">
            <VerdictList />
          </div>

          <div data-reveal className="mt-24">
            <p className="chapter-kicker text-[var(--accent)]">
              {EVIDENCE.engineKicker}
            </p>
            <h3
              data-words
              className="mt-3 font-display text-3xl font-light text-white sm:text-4xl"
            >
              {EVIDENCE.engineHeading}
            </h3>
            <p className="mt-4 max-w-2xl text-sm leading-relaxed text-[#93a3ba]">
              {EVIDENCE.engineBody}
            </p>
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
                  <span
                    className={`shrink-0 font-mono text-[10px] tracking-[0.12em] ${
                      p.live ? "text-[var(--accent)]" : "text-white/30"
                    }`}
                  >
                    {p.code}
                  </span>
                  <span
                    className={`truncate text-[12px] tracking-tight ${
                      p.live ? "text-white" : "text-white/45"
                    }`}
                  >
                    {p.name}
                  </span>
                  <span className="ml-auto flex shrink-0 items-center gap-1.5">
                    <span
                      className={`block h-1 w-1 rounded-full ${
                        p.live
                          ? "bg-[#8fd8c8] shadow-[0_0_8px_rgba(143,216,200,0.8)]"
                          : "bg-white/15"
                      }`}
                      aria-hidden
                    />
                    <span
                      className={`text-[8px] font-medium uppercase tracking-[0.2em] ${
                        p.live ? "text-[#8fd8c8]" : "text-white/25"
                      }`}
                    >
                      {p.live ? "live" : "planned"}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-5 text-[11px] leading-relaxed text-white/35">
              Status as of the current repository:{" "}
              <span className="text-white/55">earthquake_coseismic</span> and{" "}
              <span className="text-white/55">tsunami_ocean_evidence</span> are
              implemented; the rest are scaffolded pipelines on the roadmap, each
              with its own manifest, fetch layer and status vocabulary.
            </p>
          </div>
        </div>
      </section>

      {/* chapter VI — what makes Speckle different (the SAR pass) */}
      <Innovation />

      {/* chapter VII — the architecture, taken apart */}
      <Architecture />

      {/* chapter VIII — from results to reasons */}
      <Correlations />

      {/* chapter IX — what it makes possible */}
      <Impact />

      {/* chapter X — the view from home */}
      <HomeImpact />

      {/* chapter XI — the closing title card, racking into focus */}
      <section
        ref={finaleRef}
        aria-labelledby="finale-heading"
        className="relative z-[2] overflow-hidden"
        style={{ ["--accent" as string]: THEMES.finale.accent }}
      >
        <div
          data-kenburns="0.16"
          aria-hidden
          className="absolute -inset-y-[6%] inset-x-0 opacity-55"
          style={{
            backgroundImage: "url(/landscapes/descent.webp)",
            backgroundSize: "cover",
            backgroundPosition: "center 62%",
          }}
        />
        {/* blend in from the home dark, settle into the footer dark */}
        <div
          aria-hidden
          className="absolute inset-x-0 top-0 h-[32svh] bg-gradient-to-b from-[#04070d] via-[#04070d]/55 to-transparent"
        />
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-[30svh] bg-gradient-to-b from-transparent via-[#04070d]/55 to-[#04070d]"
        />
        <div aria-hidden className="absolute inset-0 bg-[#04070d]/30" />
        <div aria-hidden className="grain pointer-events-none absolute inset-0" />
        <div className="relative z-10 mx-auto flex min-h-[92svh] max-w-3xl flex-col items-center justify-center px-5 py-24 text-center sm:px-8">
          <p
            ref={finaleKicker}
            className="chapter-kicker will-change-transform"
          >
            {FINALE.kicker}
          </p>
          <h2
            ref={finaleHeading}
            id="finale-heading"
            className="mt-5 font-display text-5xl font-light leading-[1.05] text-white will-change-transform sm:text-6xl"
          >
            {FINALE.heading}
          </h2>
          <p
            data-reveal
            className="mx-auto mt-6 max-w-xl text-sm leading-relaxed text-[#a5b3c7] sm:text-base"
          >
            {FINALE.body}
          </p>
          <div data-reveal className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
            <a
              href={FINALE.primary.href}
              target="_blank"
              rel="noreferrer noopener"
              className="group inline-flex items-center gap-2.5 rounded-full bg-white px-8 py-4 text-[12px] font-semibold uppercase tracking-[0.2em] text-[#0a1220] transition-all duration-300 hover:bg-[var(--accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)]"
            >
              {FINALE.primary.label}
              <ArrowRight
                className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-1"
                aria-hidden
              />
            </a>
            <a
              href={FINALE.secondary.href}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-2.5 rounded-full border border-white/30 px-8 py-4 text-[12px] font-semibold uppercase tracking-[0.2em] text-white/90 transition-all duration-300 hover:border-[var(--accent)] hover:bg-white/[0.05] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent)]"
            >
              {FINALE.secondary.label}
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}
