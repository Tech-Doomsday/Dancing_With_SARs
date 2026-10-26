'use client';

/**
 * Dancing with SARs — application shell.
 *
 * One app, two views, no second application shell:
 *   · Engine overview — the nisar-hazard-engine (Python) presented honestly
 *     from its own pipeline manifests (the primary workflow of the repo).
 *   · Earthquake analysis — the MOTUS module (ingress analysis, evidence,
 *     tsunami) that makes pipelines 3.01 / 3.14 interactive.
 *
 * The header, footer and methodology drawer are shared; switching views is
 * plain state, so the app stays a single route and every deep feature
 * (guided tour, tsunami panel, pair finder) keeps working exactly as it did
 * in the standalone module.
 */

import { useCallback, useEffect, useState } from 'react';
import { BookOpen, Radar } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { MethodologyDrawer } from '@/components/app/methodology';
import { EngineOverview } from '@/components/app/engine-overview';
import { EarthquakeAnalysis } from '@/components/app/earthquake-analysis';
import type { HealthPayload } from '@/components/app/types';

type Section = 'overview' | 'earthquake';

export default function Home() {
  const [section, setSection] = useState<Section>('overview');
  const [health, setHealth] = useState<HealthPayload | null>(null);
  /** One-shot deep-link flag: open the earthquake section at the tsunami panel. */
  const [tsunamiFocus, setTsunamiFocus] = useState(false);

  useEffect(() => {
    fetch('/api')
      .then((r) => r.json())
      .then((d: HealthPayload) => setHealth(d))
      .catch(() => setHealth(null));
  }, []);

  const goto = useCallback((s: Section) => {
    setSection(s);
    window.scrollTo({ top: 0 });
  }, []);

  const openEarthquake = useCallback(() => goto('earthquake'), [goto]);
  const openTsunami = useCallback(() => {
    setTsunamiFocus(true);
    goto('earthquake');
  }, [goto]);

  return (
    <div className="flex min-h-screen flex-col bg-[#FAF9F6]">
      <header className="sticky top-0 z-40 border-b border-[#E7E2DA] bg-white/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-2 px-4 py-2.5 sm:h-14 sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:py-0 sm:px-6">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#1C1A17]" aria-hidden>
                <Radar className="h-4 w-4 text-[#B08D57]" />
              </span>
              <span className="leading-tight">
                <span className="block text-[15px] font-semibold tracking-[0.01em] text-[#1C1A17]">
                  Dancing with SARs
                </span>
                <span className="hidden font-mono text-[9.5px] uppercase tracking-[0.12em] text-[#8A837B] sm:block">
                  nisar-hazard-engine
                </span>
              </span>
            </div>
            {/* On mobile the methodology trigger rides on the brand row… */}
            <div className="sm:hidden">
              <MethodologyDrawer
                trigger={
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 gap-1.5 rounded-full border-[#D8D2C6] px-3 text-[11px] font-normal text-[#4A443C] hover:bg-[#F7F4EE]"
                  >
                    <BookOpen className="h-3.5 w-3.5" aria-hidden />
                    How it works
                  </Button>
                }
              />
            </div>
          </div>

          <nav aria-label="Application sections" className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => goto('overview')}
              aria-current={section === 'overview' ? 'page' : undefined}
              className={`h-9 rounded-lg px-3.5 text-[12.5px] font-medium transition-colors sm:h-8 ${
                section === 'overview'
                  ? 'bg-[#1C1A17] text-white'
                  : 'bg-white text-[#6F6862] hover:bg-[#F7F4EE] hover:text-[#1C1A17]'
              }`}
            >
              Engine overview
            </button>
            <button
              type="button"
              onClick={() => goto('earthquake')}
              aria-current={section === 'earthquake' ? 'page' : undefined}
              className={`h-9 rounded-lg px-3.5 text-[12.5px] font-medium transition-colors sm:h-8 ${
                section === 'earthquake'
                  ? 'bg-[#1C1A17] text-white'
                  : 'bg-white text-[#6F6862] hover:bg-[#F7F4EE] hover:text-[#1C1A17]'
              }`}
            >
              Earthquake analysis
            </button>
            {/* …on desktop it sits with the tabs. */}
            <div className="hidden sm:block">
              <MethodologyDrawer
                trigger={
                  <Button
                    variant="outline"
                    size="sm"
                    className="ml-1 h-8 gap-1.5 rounded-full border-[#D8D2C6] px-3 text-[11px] font-normal text-[#4A443C] hover:bg-[#F7F4EE]"
                  >
                    <BookOpen className="h-3.5 w-3.5" aria-hidden />
                    How it works
                  </Button>
                }
              />
            </div>
          </nav>
        </div>
      </header>

      {section === 'overview' ? (
        <EngineOverview health={health} onOpenEarthquake={openEarthquake} onOpenTsunami={openTsunami} />
      ) : (
        <EarthquakeAnalysis
          health={health}
          focusTsunami={tsunamiFocus}
          onTsunamiFocusHandled={() => setTsunamiFocus(false)}
        />
      )}

      <footer className="mt-auto border-t border-[#E7E2DA] bg-white">
        <div className="mx-auto max-w-[1240px] px-4 py-5 sm:px-6">
          <p className="text-[11px] leading-4 text-[#8A837B]">
            Dancing with SARs · <span className="font-mono">nisar-hazard-engine</span> + the MOTUS
            earthquake-analysis module (NASA Space Apps Challenge 2026). Data: NASA CMR · ASF DAAC ·
            USGS ComCat · NISAR sample products (ALOS PALSAR via the NISAR SDS) · NOAA NDBC DART.
            This is a scientific demonstration — not an operational service. All areas shown are
            real NASA data; the synthetic validation fixtures that test the pipeline are never shown
            as study areas.
            {health && !health.capabilities.liveUsgs && ' (Live earthquake catalog currently unreachable — cached records are used.)'}
          </p>
        </div>
      </footer>
    </div>
  );
}
