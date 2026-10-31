"use client";

/** Landing (UI-build-2 §2) — public, plain language. No jargon without a one-line explanation,
 *  no map and no maplibre chunk on this page. The live status strip reports ONLY what the engine
 *  and the verdict cache state: engine up/down, scans enabled, number of cached areas. If the
 *  engine is down it says so and shows NO numbers — never invented statistics. */
import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, ArrowRight, BookOpen, Globe2, Layers, Radar, Search, SlidersHorizontal, Trophy } from "lucide-react";
import { api } from "@/lib/api";
import { cache } from "@/lib/cache";
import type { Health } from "@/lib/types";

type Status =
  | { state: "loading" }
  | { state: "down"; error: string }
  | { state: "up"; health: Health; cachedAreas: number | null };

const STEPS = [
  {
    icon: Search,
    title: "1 · Find radar scenes",
    body: "NISAR — a NASA–ISRO radar satellite — images every bit of land twice every 12 days. The engine lists exactly which radar scenes cover the place you care about, and when.",
  },
  {
    icon: SlidersHorizontal,
    title: "2 · Prepare them",
    body: "The scenes are clipped to a tile, aligned to one grid, and turned into comparable measurements (a 13-step preparation chain, recorded step by step).",
  },
  {
    icon: Radar,
    title: "3 · Run each hazard check",
    body: "Every hazard model — earthquakes first — gets the prepared data and answers with a status in plain words, plus the evidence behind it.",
  },
  {
    icon: Trophy,
    title: "4 · Rank the evidence",
    body: "Answers are grouped into tiers (from “supported by evidence” to “no signal”) so the strongest evidence is visible first.",
  },
];

const GLOSSARY = [
  ["GUNW", "a pair of two dates compared — one radar picture minus the other, so motion between the two dates shows"],
  ["GCOV", "one date's backscatter picture — how bright the ground looked to the radar that day"],
  ["tile", "the ~10 km map grid cell the engine processes at a time (an MGRS tile id like 11SLT66)"],
  ["stack", "radar scenes taken along the same orbit path, so their dates can be compared"],
  ["tier", "the engine's word for “how strongly the evidence supports this finding”, in words rather than a percentage"],
];

export default function LandingPage() {
  const [status, setStatus] = useState<Status>({ state: "loading" });

  useEffect(() => {
    const ctrl = new AbortController();
    api.health()
      .then(async (h) => {
        // cached areas: the verdict cache's own count; null when the cache is not configured
        let cachedAreas: number | null = null;
        try {
          const pins = await cache.pins(1);
          cachedAreas = pins.total;
        } catch {
          cachedAreas = null;      // cache not configured / unreachable — shown as "—"
        }
        setStatus({ state: "up", health: h, cachedAreas });
      })
      .catch((e) => setStatus({ state: "down", error: e instanceof Error ? e.message : String(e) }));
    return () => ctrl.abort();
  }, []);

  return (
    <div className="space-y-10 pb-6">
      {/* 1 · promise + primary button */}
      <section className="rounded-xl border border-[#e7e2da] bg-white p-6 sm:p-8">
        <p className="text-xs font-medium uppercase tracking-widest text-[#8a837b]">
          nisar-hazard-engine — public overview
        </p>
        <h1 className="mt-2 max-w-3xl text-2xl font-semibold leading-snug text-[#1c1a17] sm:text-3xl">
          Watching NISAR radar data for signs of ground hazards — and telling you how strong the
          evidence is, in plain words.
        </h1>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Link
            href="/map"
            className="flex items-center gap-2 rounded-md bg-[#2c5f58] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#37736a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#2c5f58]"
          >
            <Globe2 size={16} aria-hidden /> Open the Map <ArrowRight size={14} aria-hidden />
          </Link>
          <a href="#how-to-read" className="text-sm text-[#2c5f58] underline hover:text-[#1c1a17]">
            how to read a result
          </a>
        </div>
      </section>

      {/* 6 · live status strip (health + verdict cache) */}
      <section aria-label="live status" className="rounded-lg border border-[#e7e2da] bg-[#faf9f6] px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[#1c1a17]">
          <Activity size={14} aria-hidden /> Live status
        </h2>
        {status.state === "loading" && (
          <p className="mt-1 text-xs text-[#8a837b]" role="status">checking the engine…</p>
        )}
        {status.state === "down" && (
          <p className="mt-1 text-xs text-[#9e3b2c]" role="alert">
            the engine is down right now ({status.error}) — no numbers are shown, because none
            could be verified. The public pages you can still read here stay honest.
          </p>
        )}
        {status.state === "up" && (
          <dl className="mt-2 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
            <div className="rounded border border-[#e7e2da] bg-white px-3 py-2">
              <dt className="text-[#8a837b]">engine</dt>
              <dd className="font-medium text-[#1c1a17]">up</dd>
            </div>
            <div className="rounded border border-[#e7e2da] bg-white px-3 py-2">
              <dt className="text-[#8a837b]">scans enabled</dt>
              <dd className="font-medium text-[#1c1a17]">{status.health.scans_enabled ? "yes" : "no"}</dd>
            </div>
            <div className="rounded border border-[#e7e2da] bg-white px-3 py-2">
              <dt className="text-[#8a837b]">cached areas</dt>
              <dd className="font-medium text-[#1c1a17]">
                {status.cachedAreas != null ? String(status.cachedAreas) : "—"}
                {status.cachedAreas == null && (
                  <span className="ml-1 text-[#8a837b]">(verdict cache not connected)</span>
                )}
              </dd>
            </div>
          </dl>
        )}
      </section>

      {/* 2 · what this is */}
      <section aria-labelledby="what-this-is" className="max-w-3xl">
        <h2 id="what-this-is" className="text-lg font-semibold text-[#1c1a17]">What this is</h2>
        <p className="mt-2 text-sm leading-relaxed text-[#3d3a35]">
          A system that watches NISAR radar data for signs of hazards — earthquakes first — and, for
          each place and each hazard model, says how strongly the evidence supports it. It is a
          demonstration of honest machine-assisted hazard triage: every finding names the data it
          used, every number traces back to a measurement, and what the system does not know is
          shown as unknown rather than papered over.
        </p>
        <p className="mt-2 text-sm leading-relaxed text-[#3d3a35]">
          Nothing here decides anything by itself. The engine ranks evidence; a human reads it.
        </p>
      </section>

      {/* 3 · how it works */}
      <section aria-labelledby="how-it-works">
        <h2 id="how-it-works" className="text-lg font-semibold text-[#1c1a17]">How it works</h2>
        <ol className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s) => (
            <li key={s.title} className="rounded-lg border border-[#e7e2da] bg-white p-4">
              <p className="flex items-center gap-2 text-sm font-semibold text-[#2c5f58]">
                <s.icon size={15} aria-hidden /> {s.title}
              </p>
              <p className="mt-1.5 text-xs leading-relaxed text-[#3d3a35]">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* 4 · how to read a result */}
      <section id="how-to-read" aria-labelledby="how-to-read-h" className="max-w-3xl">
        <h2 id="how-to-read-h" className="text-lg font-semibold text-[#1c1a17]">How to read a result</h2>
        <p className="mt-2 text-sm leading-relaxed text-[#3d3a35]">
          Every finding carries a tier — a word, not a percentage: <strong>supported</strong> (the
          evidence backs it), <strong>candidate</strong> (interesting but uncatalogued),
          {" "}<strong>not supported</strong>, <strong>insufficient</strong> (not enough good data to
          say), and <strong>quiet</strong> (cleanly nothing). The number under a finding is a
          rank score — it orders findings inside one tier. It is <em>not</em> a probability, and the
          Map shows that warning next to every score.
        </p>
        <p className="mt-2 text-sm leading-relaxed text-[#3d3a35]">
          When a scan runs on synthetic or fixture data instead of real observations, the Map labels
          it <strong>SYNTHETIC</strong> — synthetic data never masquerades as a real answer.
        </p>
      </section>

      {/* 5 · mini glossary */}
      <section aria-labelledby="glossary">
        <h2 id="glossary" className="flex items-center gap-2 text-lg font-semibold text-[#1c1a17]">
          <BookOpen size={16} aria-hidden /> Mini glossary
        </h2>
        <dl className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {GLOSSARY.map(([term, def]) => (
            <div key={term} className="rounded-lg border border-[#e7e2da] bg-white p-3">
              <dt className="text-sm font-semibold text-[#1c1a17]">{term}</dt>
              <dd className="mt-1 text-xs leading-relaxed text-[#3d3a35]">{def}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* 7 · footer note lives in the site layout; add the emergency-use line here explicitly */}
      <section className="rounded-lg border border-[#9e3b2c]/30 bg-[#faf1ee] px-4 py-3">
        <p className="flex items-start gap-2 text-xs leading-relaxed text-[#9e3b2c]">
          <Layers size={14} aria-hidden className="mt-0.5 shrink-0" />
          This is a scientific demonstration, not an operational service — <strong>not for
          emergency decisions</strong>. For earthquakes use your national seismic network; for
          floods and fires follow your civil-protection authorities.
        </p>
      </section>
    </div>
  );
}
