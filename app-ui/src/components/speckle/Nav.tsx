"use client";

/**
 * Minimal navigation — the quietest possible header. The wordmark on the
 * left, the console entry point on the right. Nothing else: the story
 * speaks for itself, the chapter compass lives on the right edge of the
 * stage, and this bar never competes with either.
 */

import { useEffect, useState } from "react";
import { ArrowUpRight, Github } from "lucide-react";
import { LINKS } from "./data";

function jumpToTop() {
  const spacer = document.getElementById("speckle-story-spacer");
  const storyH = spacer?.offsetHeight ?? window.innerHeight * 9.5;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

export default function Nav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 80);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-all duration-500 ${
        scrolled
          ? "border-b border-white/[0.06] bg-[#04070d]/60 backdrop-blur-md"
          : "border-b border-transparent"
      }`}
    >
      <nav
        aria-label="Primary"
        className="mx-auto flex h-16 w-full max-w-7xl items-center justify-between px-5 sm:px-8"
      >
        <button
          type="button"
          onClick={jumpToTop}
          className="font-display text-lg font-medium tracking-[0.34em] text-white transition-opacity hover:opacity-70"
        >
          SPECKLE
        </button>

        <div className="flex items-center gap-5 sm:gap-7">
          <a
            href={LINKS.repo}
            target="_blank"
            rel="noreferrer noopener"
            aria-label="GitHub repository"
            className="text-white/50 transition-colors hover:text-white"
          >
            <Github className="h-[15px] w-[15px]" aria-hidden />
          </a>
          <a
            href={LINKS.app}
            target="_blank"
            rel="noreferrer noopener"
            className="group inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.24em] text-white/80 transition-colors hover:text-white"
          >
            Console
            <ArrowUpRight
              className="h-3 w-3 transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
              aria-hidden
            />
          </a>
        </div>
      </nav>
    </header>
  );
}
