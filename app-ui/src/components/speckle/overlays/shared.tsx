"use client";

/**
 * Shared overlay machinery for every chapter of the story.
 *
 * An Overlay is a fixed, full-screen layer whose opacity and drift are
 * written directly to the DOM from the shared animation loop — React never
 * re-renders during the journey. Layers marked `interactive` unlock
 * pointer events and tab order only while they are actually on stage.
 */

import { useEffect, useRef, type ReactNode } from "react";
import { addDriver, smoothstep, storyState } from "../scroll";

export interface OverlayProps {
  /** [fadeInStart, fullIn, fullOutStart, fadeOutEnd] in story progress */
  range: [number, number, number, number];
  className?: string;
  /** allow pointer events + focus while visible (CTAs, sliders) */
  interactive?: boolean;
  /** upward drift on entry, px (exit drift is 1.25×) */
  drift?: number;
  children: ReactNode;
}

export function Overlay({
  range,
  className,
  interactive = false,
  drift = 34,
  children,
}: OverlayProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const [a, b, c, d] = range;
    return addDriver((p) => {
      const el = ref.current;
      if (!el) return;
      const reduce = storyState.reduced;
      const inn = smoothstep(a, b, p);
      const out = 1 - smoothstep(c, d, p);
      const opacity = Math.min(inn, out);
      const y = reduce
        ? 0
        : (1 - inn) * drift - (1 - out) * drift * 1.25;
      el.style.opacity = opacity.toFixed(3);
      el.style.transform = `translate3d(0, ${y.toFixed(1)}px, 0)`;
      const hidden = opacity <= 0.004;
      el.style.visibility = hidden ? "hidden" : "visible";
      if (interactive) {
        el.style.pointerEvents = opacity > 0.5 ? "auto" : "none";
        el.inert = opacity <= 0.5;
      }
    });
  }, [range, interactive, drift]);

  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: 0,
        visibility: "hidden",
        ...(interactive ? { pointerEvents: "none" as const } : null),
      }}
      {...(interactive ? { inert: true } : null)}
    >
      {children}
    </div>
  );
}
