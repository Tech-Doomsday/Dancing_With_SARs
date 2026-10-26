'use client';

/**
 * Guided demo tour — a "Start here" walk through the three REAL MOTUS
 * stories in about three minutes, doubling as the script for the demo video.
 *
 *  Story 1 · Mojave 2026 (real, recorded)      — the honest negative.
 *  Story 2 · Los Angeles 2008 (real, live)     — the NASA sample product, run live.
 *  Story 3 · Panama M7.7 (real, evidence)      — real event, real data, model prediction.
 *
 * The tour drives the real page: steps can select a dataset, start an
 * analysis, or open the recorded result. Nothing is faked — the narration
 * describes exactly what the user sees.
 */

import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Play, X, ChevronLeft, ChevronRight, BookOpen, Copy, Check, Clock, Mountain, Globe2, Landmark } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogDescription } from '@/components/ui/dialog';

export type TourAction =
  | { type: 'select'; datasetId: string }
  | { type: 'run' }
  | { type: 'recorded' }
  | { type: 'none' };

export interface TourStep {
  /** Element id to scroll to + highlight. */
  target: string;
  /** What the page should do when this step is entered. */
  action: TourAction;
  narration: string;
  /** Short label for the progress dots. */
  at: string;
}

export interface TourStory {
  id: string;
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  durationMin: number;
  datasetId: string;
  steps: TourStep[];
}

export const TOUR_STORIES: TourStory[] = [
  {
    id: 'mojave',
    title: 'Mojave 2026 — the honest answer',
    subtitle: 'Real NASA radar pair. An M4.06 earthquake inside the frame. The correct result is “no motion detectable”.',
    icon: <Mountain className="h-5 w-5" aria-hidden />,
    durationMin: 1,
    datasetId: 'real-nisar-mojave-2026',
    steps: [
      {
        target: 'hero',
        action: { type: 'none' },
        at: 'Intro',
        narration:
          'This is MOTUS. It compares two radar images taken from space to check whether the ground moved between them — and it is built to say “I can’t tell” when the data cannot support an answer. This story is about a real, operational NISAR radar pair over the Mojave Desert in May–June 2026.',
      },
      {
        target: 'choose-area',
        action: { type: 'select', datasetId: 'real-nisar-mojave-2026' },
        at: 'Real data',
        narration:
          'This card is real operational NASA data: a 12-day pair (22 May → 3 June 2026) whose full 2.6 GB product was analysed when it was on the server. The analysis was recorded — every number you are about to see came from the real product, clearly labelled as a recorded result.',
      },
      {
        target: 'step-result',
        action: { type: 'recorded' },
        at: 'The verdict',
        narration:
          'The one-screen answer: NO motion detectable. The radar signal passed through a severely disturbed ionosphere, and that contamination is larger than the biggest apparent signal in the scene — so the honest verdict is “no change detected above the threshold”. Making no claim here is not a failure — it is the scientifically correct result.',
      },
      {
        target: 'seismic-context',
        action: { type: 'none' },
        at: 'The earthquake',
        narration:
          'And there IS an earthquake in this story: a real M4.06 struck inside the radar frame between the two images. MOTUS reports “insufficient evidence” — the ionosphere noise is bigger than the signal, so attributing anything to the quake would be guessing. Honest, not sensational.',
      },
      {
        target: 'scope-note',
        action: { type: 'none' },
        at: 'Why it matters',
        narration:
          'That honesty is the point of the tool: MOTUS quantifies what it cannot know. Next — a story where the evidence is much richer.',
      },
    ],
  },
  {
    id: 'la2008',
    title: 'Los Angeles 2008 — the NASA sample product',
    subtitle: 'A real L-band radar pair over the San Gabriel Mountains, processed by the NISAR science team. Runs live, right here.',
    icon: <Landmark className="h-5 w-5" aria-hidden />,
    durationMin: 1,
    datasetId: 'real-nisar-la-2008',
    steps: [
      {
        target: 'choose-area',
        action: { type: 'select', datasetId: 'real-nisar-la-2008' },
        at: 'Real sample data',
        narration:
          'This is the open NISAR sample product: a real ALOS PALSAR L-band pair from October–November 2008 over the Los Angeles region, converted into NISAR format by the NISAR science data system. Unlike the Mojave story, the full data is on this server — so the analysis runs LIVE.',
      },
      {
        target: 'step-run',
        action: { type: 'run' },
        at: 'Live run',
        narration:
          'The real pipeline is running on real radar data, right now: quality masking, phase-to-movement conversion, uncertainty estimation, the detection rule — and a live cross-check against the USGS earthquake catalog for anything that shook between the two radar passes.',
      },
      {
        target: 'step-result',
        action: { type: 'none' },
        at: 'The verdict',
        narration:
          'The honest answer for this 46-day pair: INCONCLUSIVE — only isolated pixels crossed the threshold, far below the minimum credible area. In radar terms the scene stayed coherent but nothing moved beyond the noise. A tool that can say “inconclusive” on real data is a tool you can trust when it says “detected”.',
      },
      {
        target: 'map-section',
        action: { type: 'none' },
        at: 'Explore the map',
        narration:
          'Drag the Before/After divider on the map, and hover to read exact values. This is the same map a scientist would explore — try the “Detected change” layer for the plainest view of what passed the threshold.',
      },
    ],
  },
  {
    id: 'panama',
    title: 'Panama M7.7 — real evidence',
    subtitle: 'A magnitude-7.7 earthquake with real NASA radar coverage. Evidence, official imagery, and a labelled model prediction.',
    icon: <Globe2 className="h-5 w-5" aria-hidden />,
    durationMin: 1,
    datasetId: 'evidence-panama-2026',
    steps: [
      {
        target: 'choose-area',
        action: { type: 'select', datasetId: 'evidence-panama-2026' },
        at: 'The event',
        narration:
          'On 9 October 2026, a magnitude 7.7 earthquake struck Panama — one of the largest events of the year. NASA’s NISAR radar covered the region with multiple 12-day pairs. This story shows what real evidence looks like before any measurement is made.',
      },
      {
        target: 'step-run',
        action: { type: 'run' },
        at: 'Real records',
        narration:
          'Everything assembling here is real: the USGS event record, the actual NISAR granule list straight from NASA’s live catalog, and the official radar browse imagery rendered by the NISAR science data system.',
      },
      {
        target: 'step-result',
        action: { type: 'none' },
        at: 'Model, not measurement',
        narration:
          'Because downloading the full science products needs a (free) NASA Earthdata login, this view claims NO measurement. Instead it shows what a standard earthquake model predicts — computed from the published fault information, and labelled “model prediction, not a measurement” everywhere it appears.',
      },
      {
        target: 'map-section',
        action: { type: 'none' },
        at: 'The prediction',
        narration:
          'On the map: the predicted movement toward (brown) and away from (green) the satellite. Compare it with the official radar imagery above — that is the workflow a scientist would follow to decide whether this pair is worth downloading.',
      },
    ],
  },
];

/** Full demo-video script text (all stories). */
export function demoScriptText(): string {
  const lines: string[] = [
    'MOTUS — DEMO VIDEO SCRIPT (approx. 3 minutes)',
    '================================================',
    '',
  ];
  for (const story of TOUR_STORIES) {
    lines.push(`STORY: ${story.title}`);
    lines.push(`(${story.subtitle})`);
    lines.push('');
    story.steps.forEach((s, i) => {
      lines.push(`[${String(i + 1).padStart(2, '0')}] ${s.at.toUpperCase()}`);
      lines.push(s.narration);
      lines.push('');
    });
    lines.push('------------------------------------------------');
    lines.push('');
  }
  lines.push('END OF SCRIPT — motus · NASA Space Apps 2026 · "Dancing with the SARs"');
  return lines.join('\n');
}

/* ------------------------------------------------------------------ */

export function StoryPicker({
  open,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onPick: (storyIdx: number) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg rounded-xl border-[#E7E2DA] bg-white p-0">
        <DialogHeader className="border-b border-[#EFEAE2] px-5 py-4">
          <DialogTitle className="text-[16px] font-semibold text-[#1C1A17]">Take the guided tour</DialogTitle>
          <DialogDescription className="text-[12.5px] leading-[1.45rem] text-[#6F6862]">
            Three short real-data stories, about three minutes total. The tour drives the real page — nothing is a mockup.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2.5 px-5 py-4">
          {TOUR_STORIES.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onPick(i)}
              className="flex w-full items-start gap-3.5 rounded-xl border border-[#E7E2DA] bg-white p-4 text-left transition-all hover:border-[#1C1A17] hover:shadow-[0_1px_8px_rgba(28,26,23,0.08)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#1C1A17]"
            >
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#F7F2E7] text-[#7A6438]">
                {s.icon}
              </span>
              <span className="min-w-0">
                <span className="flex items-baseline gap-2">
                  <span className="text-[13.5px] font-semibold text-[#1C1A17]">{s.title}</span>
                  <span className="flex shrink-0 items-center gap-1 font-mono text-[10px] text-[#8A837B]">
                    <Clock className="h-3 w-3" aria-hidden />{s.durationMin} min
                  </span>
                </span>
                <span className="mt-1 block text-[12px] leading-[1.4rem] text-[#6F6862]">{s.subtitle}</span>
              </span>
            </button>
          ))}
          <DemoScriptButton />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function DemoScriptButton({ compact = false }: { compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); setCopied(false); }}>
      <DialogTrigger asChild>
        <button
          type="button"
          className={`flex items-center gap-1.5 text-[11.5px] font-medium text-[#6F6862] underline decoration-[#C9C0B2] decoration-dotted underline-offset-2 transition-colors hover:text-[#1C1A17] ${compact ? '' : 'w-full justify-center pt-1'}`}
        >
          <BookOpen className="h-3.5 w-3.5" aria-hidden />
          Read the full demo-video script
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[80vh] max-w-xl overflow-hidden rounded-xl border-[#E7E2DA] bg-white p-0">
        <DialogHeader className="border-b border-[#EFEAE2] px-5 py-4">
          <DialogTitle className="text-[15px] font-semibold text-[#1C1A17]">Demo-video script</DialogTitle>
          <DialogDescription className="text-[12px] text-[#8A837B]">
            Everything the tour narrates, in order — ready to read over a screen recording.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center justify-between border-b border-[#EFEAE2] px-5 py-2.5">
          <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-[#8A837B]">motus-demo-script.txt</span>
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1.5 rounded-full border-[#D8D2C6] px-3 text-[11px] font-normal"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(demoScriptText());
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              } catch { /* clipboard unavailable */ }
            }}
          >
            {copied ? <Check className="h-3.5 w-3.5 text-[#2C5F58]" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
            {copied ? 'Copied' : 'Copy script'}
          </Button>
        </div>
        <div className="max-h-[52vh] overflow-y-auto px-5 py-4">
          <pre className="whitespace-pre-wrap font-mono text-[11px] leading-[1.5] text-[#4A443C]">{demoScriptText()}</pre>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */

export function TourCard({
  story,
  stepIdx,
  onStep,
  onExit,
  busy,
}: {
  story: TourStory;
  stepIdx: number;
  onStep: (i: number) => void;
  onExit: () => void;
  busy?: boolean;
}) {
  const step = story.steps[stepIdx];
  const last = stepIdx === story.steps.length - 1;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onExit();
      if (e.key === 'ArrowRight') onStep(Math.min(story.steps.length - 1, stepIdx + 1));
      if (e.key === 'ArrowLeft') onStep(Math.max(0, stepIdx - 1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [stepIdx, story.steps.length, onExit, onStep]);

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 24 }}
        transition={{ duration: 0.3, ease: 'easeOut' }}
        className="fixed bottom-4 left-1/2 z-[60] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 sm:bottom-6"
        role="dialog"
        aria-label={`Guided tour — ${story.title}, step ${stepIdx + 1} of ${story.steps.length}`}
      >
        <div className="rounded-2xl border border-[#E7E2DA] bg-white/97 p-4 shadow-[0_8px_40px_rgba(28,26,23,0.18)] backdrop-blur-md">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[#1C1A17] text-white">
                <Play className="h-3 w-3" aria-hidden />
              </span>
              <span className="truncate text-[12px] font-semibold text-[#1C1A17]">{story.title}</span>
            </div>
            <button
              type="button"
              onClick={onExit}
              aria-label="Exit tour"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[#8A837B] transition-colors hover:bg-[#F7F4EE] hover:text-[#1C1A17]"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>

          {/* progress */}
          <div className="mt-2.5 flex items-center gap-1.5" aria-hidden>
            {story.steps.map((s, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onStep(i)}
                aria-label={`Go to step: ${s.at}`}
                className={`h-1.5 flex-1 rounded-full transition-colors ${i === stepIdx ? 'bg-[#1C1A17]' : i < stepIdx ? 'bg-[#B08D57]' : 'bg-[#E7E2DA]'}`}
              />
            ))}
          </div>

          <p className="mt-3 text-[13px] leading-[1.5rem] text-[#4A443C]">
            {busy ? (
              <span className="flex items-center gap-2 text-[#8A837B]">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#D8D2C6] border-t-[#B08D57]" aria-hidden />
                Running the real analysis for this step…
              </span>
            ) : (
              step.narration
            )}
          </p>

          <div className="mt-3.5 flex items-center justify-between gap-3">
            <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-[#8A837B]">
              {step.at} · {stepIdx + 1}/{story.steps.length}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={stepIdx === 0}
                onClick={() => onStep(stepIdx - 1)}
                className="h-8 gap-1 rounded-lg border-[#D8D2C6] px-3 text-[12px] font-normal text-[#4A443C]"
                aria-label="Previous tour step"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden /> Back
              </Button>
              <Button
                size="sm"
                onClick={() => (last ? onExit() : onStep(stepIdx + 1))}
                className="h-8 gap-1 rounded-lg bg-[#1C1A17] px-4 text-[12px] font-medium text-white hover:bg-[#33302B]"
              >
                {last ? 'Finish' : 'Next'}
                {!last && <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
              </Button>
            </div>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
