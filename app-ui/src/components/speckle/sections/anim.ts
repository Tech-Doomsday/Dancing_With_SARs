"use client";

/**
 * Shared GSAP choreography for the in-flow chapters — the grammar of the
 * film. Title cards rise word by word from masks, hairlines draw
 * themselves in, content settles with a soft rack-focus blur, imagery
 * drifts with scrubbed Ken Burns, and lists cascade in sequence.
 * Everything is disabled under prefers-reduced-motion.
 */

import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

export const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------------ */
/* word masks — the title-card reveal                                   */
/* ------------------------------------------------------------------ */

/**
 * Wrap each word of an element's plain text in an overflow-hidden mask
 * (`.w-mask > .w-mask-i`, see globals.css). The element keeps its meaning
 * for assistive tech through an aria-label; the masks are presentation
 * only. Returns the inner spans, ready to rise.
 */
function splitWords(el: HTMLElement): HTMLElement[] {
  const text = (el.textContent ?? "").replace(/\s+/g, " ").trim();
  el.setAttribute("aria-label", text);
  el.textContent = "";
  const inners: HTMLElement[] = [];
  for (const word of text.split(" ")) {
    const mask = document.createElement("span");
    mask.className = "w-mask";
    mask.setAttribute("aria-hidden", "true");
    const inner = document.createElement("span");
    inner.className = "w-mask-i";
    inner.textContent = word;
    mask.appendChild(inner);
    el.appendChild(mask);
    el.appendChild(document.createTextNode(" "));
    inners.push(inner);
  }
  return inners;
}

/* ------------------------------------------------------------------ */
/* cinematify — attach the film grammar to a section                    */
/* ------------------------------------------------------------------ */

/** Attach cinematic scroll behaviour to a section. Returns a cleanup fn. */
export function cinematify(section: HTMLElement): () => void {
  if (prefersReducedMotion()) return () => undefined;

  const ctx = gsap.context(() => {
    /* quiet parallax drift on full-bleed media */
    section.querySelectorAll<HTMLElement>("[data-parallax]").forEach((bg) => {
      gsap.fromTo(
        bg,
        { yPercent: -6 },
        {
          yPercent: 6,
          ease: "none",
          scrollTrigger: {
            trigger: bg.closest("section, [data-frame]") ?? bg,
            start: "top bottom",
            end: "bottom top",
            scrub: 0.6,
          },
        },
      );
    });

    /* scrubbed Ken Burns on media frames */
    section.querySelectorAll<HTMLElement>("[data-kenburns]").forEach((el) => {
      const amt = parseFloat(el.dataset.kenburns ?? "0.14") || 0.14;
      gsap.fromTo(
        el,
        { scale: 1 + amt, yPercent: -3.5 },
        {
          scale: 1.02,
          yPercent: 3,
          ease: "none",
          scrollTrigger: {
            trigger: el.parentElement ?? el,
            start: "top bottom",
            end: "bottom top",
            scrub: 0.65,
          },
        },
      );
    });

    /* title cards — the words rise from their masks */
    section.querySelectorAll<HTMLElement>("[data-words]").forEach((el) => {
      const inners = splitWords(el);
      if (!inners.length) return;
      gsap.fromTo(
        inners,
        { yPercent: 118 },
        {
          yPercent: 0,
          duration: 1.15,
          ease: "power4.out",
          stagger: 0.055,
          scrollTrigger: { trigger: el, start: "top 78%", once: true },
        },
      );
    });

    /* hairlines that draw themselves in */
    section.querySelectorAll<HTMLElement>("[data-draw]").forEach((el) => {
      gsap.fromTo(
        el,
        { scaleX: 0 },
        {
          scaleX: 1,
          duration: 1.25,
          ease: "power3.inOut",
          scrollTrigger: { trigger: el, start: "top 86%", once: true },
        },
      );
    });

    /* reveals — each block settles into focus as it enters */
    section.querySelectorAll<HTMLElement>("[data-reveal]").forEach((el) => {
      gsap.fromTo(
        el,
        { autoAlpha: 0, y: 32, filter: "blur(7px)" },
        {
          autoAlpha: 1,
          y: 0,
          filter: "blur(0px)",
          duration: 1.05,
          ease: "power3.out",
          scrollTrigger: { trigger: el, start: "top 84%" },
        },
      );
    });

    /* cascades — children flow in as a sequence */
    section.querySelectorAll<HTMLElement>("[data-stagger]").forEach((box) => {
      const kids = Array.from(box.children) as HTMLElement[];
      if (!kids.length) return;
      gsap.fromTo(
        kids,
        { autoAlpha: 0, y: 16 },
        {
          autoAlpha: 1,
          y: 0,
          duration: 0.7,
          ease: "power2.out",
          stagger: 0.045,
          scrollTrigger: { trigger: box, start: "top 86%", once: true },
        },
      );
    });
  }, section);

  return () => ctx.revert();
}
