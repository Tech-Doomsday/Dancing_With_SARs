"use client";

/**
 * The Speckle footer — quiet, honest, minimal. Project context, real links,
 * required credits and a clear non-endorsement statement.
 */

import { ArrowUpRight, Github } from "lucide-react";
import { FOOTER, LINKS } from "./data";

const COLUMNS: { title: string; links: { label: string; href: string; external?: boolean }[] }[] = [
  {
    title: "Story",
    links: [
      { label: "The descent", href: "#top" },
      { label: "Changing systems", href: "#systems" },
      { label: "Evidence & method", href: "#method" },
      { label: "What's different", href: "#innovation" },
      { label: "The architecture", href: "#architecture" },
      { label: "Results & reasons", href: "#correlations" },
      { label: "Impact on Bangladesh", href: "#bangladesh" },
    ],
  },
  {
    title: "Project",
    links: [
      { label: "GitHub repository", href: LINKS.repo, external: true },
      { label: "Architecture document", href: LINKS.architecture, external: true },
      { label: "Space Apps Challenge", href: LINKS.spaceapps, external: true },
    ],
  },
];

export default function Footer() {
  return (
    <footer className="relative mt-auto bg-[#04070d]">
      {/* blend in from the finale dark — no hard cut */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-[12svh] bg-gradient-to-b from-[#04070d] to-transparent"
      />
      <div className="relative mx-auto max-w-7xl px-5 pb-[max(2.5rem,env(safe-area-inset-bottom))] pt-16 sm:px-8">
        <div className="grid gap-12 md:grid-cols-12">
          <div className="md:col-span-6">
            <p className="font-display text-2xl font-light tracking-[0.3em] text-white">
              SPECKLE
            </p>
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-white/50">
              {FOOTER.blurb}
            </p>
            <a
              href={LINKS.repo}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-6 inline-flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.24em] text-white/60 transition-colors hover:text-white"
            >
              <Github className="h-4 w-4" aria-hidden />
              Dancing_With_SARs
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </a>
          </div>

          {COLUMNS.map((col) => (
            <nav
              key={col.title}
              aria-label={col.title}
              className="md:col-span-3"
            >
              <p className="text-[10px] font-semibold uppercase tracking-[0.3em] text-white/35">
                {col.title}
              </p>
              <ul className="mt-5 space-y-3">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <a
                      href={l.href}
                      {...(l.external
                        ? { target: "_blank", rel: "noreferrer noopener" }
                        : {})}
                      className="text-sm text-white/60 transition-colors hover:text-white"
                    >
                      {l.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="mt-14 flex flex-col gap-3 border-t border-white/[0.06] pt-7 text-xs leading-relaxed text-white/45 sm:flex-row sm:items-end sm:justify-between">
          <p className="max-w-xl">{FOOTER.credits}</p>
          <p className="shrink-0">{FOOTER.disclaimer}</p>
        </div>
      </div>
    </footer>
  );
}
