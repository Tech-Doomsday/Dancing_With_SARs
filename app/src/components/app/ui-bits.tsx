'use client';

/**
 * Small presentational building blocks shared across the side panel.
 *
 * FINAL-PASS NOTE (UI simplification): every badge and label here is written
 * in plain language first. The precise scientific term is kept available in
 * the `title` attribute or the expandable "What does this mean?" block so the
 * application remains scientifically useful without forcing jargon on the
 * default path.
 */

import { useState } from 'react';
import { ChevronDown, ExternalLink } from 'lucide-react';
import type { Hypothesis, UsgsEventLite } from '@/lib/science/types';
import type { DatasetAvailability } from './types';

export function MicroLabel({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] font-medium uppercase tracking-[0.08em] text-[#8A837B]">{children}</div>;
}

export function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div title={title}>
      <MicroLabel>{label}</MicroLabel>
      <div className="mt-0.5 font-mono text-[13px] tabular-nums text-[#1C1A17]">{value}</div>
    </div>
  );
}

/**
 * Inline "What does this mean?" expandable explanation.
 * Renders as a small underlined trigger; the explanation opens beneath it.
 */
export function WhatIsThis({ children, label = 'What does this mean?' }: { children: React.ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="whitespace-normal">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline items-center gap-0.5 text-[11px] font-medium text-[#6F6862] underline decoration-[#C9C0B2] decoration-dotted underline-offset-2 transition-colors hover:text-[#1C1A17]"
      >
        {label}
        <ChevronDown className={`inline h-3 w-3 align-[-2px] transition-transform duration-150 ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <span className="mt-1 block rounded-md border border-[#EFEAE2] bg-[#FDFCF9] px-2.5 py-2 text-[11.5px] leading-4 text-[#6F6862]">
          {children}
        </span>
      )}
    </span>
  );
}

/** A short plain-language note displayed directly under a heading. */
export function PlainNote({ children }: { children: React.ReactNode }) {
  return <p className="mt-1 text-[12px] leading-[1.45rem] text-[#8A837B]">{children}</p>;
}

/** One USGS earthquake record row (shared by the result and evidence views). */
export function EventRow({ ev }: { ev: UsgsEventLite }) {
  return (
    <li className="flex items-baseline justify-between gap-3 rounded-md border border-[#EFEAE2] px-3 py-2">
      <div>
        <span className="font-mono text-[12.5px] font-semibold tabular-nums text-[#9E3B2C]">M{ev.magnitude.toFixed(1)}</span>
        <span className="ml-2 text-[12px] text-[#4A443C]">{ev.place}</span>
        <div className="text-[11px] text-[#8A837B]">
          {fmtDateTime(ev.time)} · depth {ev.depthKm.toFixed(0)} km
          {ev.nodalPlane ? ` · strike ${ev.nodalPlane.strike.toFixed(0)}° dip ${ev.nodalPlane.dip.toFixed(0)}° rake ${ev.nodalPlane.rake.toFixed(0)}°` : ''}
        </div>
      </div>
      <a href={ev.url} target="_blank" rel="noopener noreferrer" className="shrink-0 text-[#6F6862] transition-colors hover:text-[#1C1A17]" aria-label={`USGS page for M${ev.magnitude} earthquake`}>
        <ExternalLink className="h-3.5 w-3.5" aria-hidden />
      </a>
    </li>
  );
}

export interface DatasetLike {
  dataClass: 'real' | 'synthetic' | 'derived';
  mode: 'quantitative' | 'evidence';
  availability: Pick<DatasetAvailability, 'state' | 'recorded'>;
}

/**
 * Plain-language readiness of a dataset, combining availability (can we run
 * anything?) with data class (is it real or a demonstration?) and whether a
 * real recorded analysis exists despite missing data. Always uses
 * text + icon, never color alone.
 */
export function readinessOf(d: DatasetLike): {
  label: string;
  icon: 'check' | 'demo' | 'unavailable' | 'recorded';
  detail: string;
} {
  if (d.availability.state !== 'available') {
    if (d.availability.recorded) {
      return {
        label: 'Recorded result',
        icon: 'recorded',
        detail: 'Real analysis recorded from the actual product — the source data is no longer on this server, but the result can be viewed.',
      };
    }
    return {
      label: 'Data unavailable',
      icon: 'unavailable',
      detail:
        d.availability.state === 'missing'
          ? 'The data files have not been downloaded to this server yet.'
          : d.availability.state === 'incomplete'
            ? 'Only some of the required data files are present.'
            : 'The downloaded data files did not pass validation.',
    };
  }
  if (d.dataClass === 'synthetic') {
    return {
      label: 'Demo data',
      icon: 'demo',
      detail: 'Simulated data with a known answer — used to demonstrate and test the analysis.',
    };
  }
  if (d.mode === 'evidence') {
    return {
      label: 'Ready to analyze',
      icon: 'check',
      detail: 'Real catalog data and official imagery — shows evidence and a model prediction, not a measurement.',
    };
  }
  return {
    label: 'Ready to analyze',
    icon: 'check',
    detail: 'Real satellite radar measurements, downloaded and ready.',
  };
}

export function ReadinessBadge({ d }: { d: DatasetLike }) {
  const r = readinessOf(d);
  const cls =
    r.icon === 'check'
      ? 'border-[#2C5F58] bg-[#EDF4F1] text-[#2C5F58]'
      : r.icon === 'demo'
        ? 'border-[#B08D57] bg-[#F7F2E7] text-[#7A6438]'
        : r.icon === 'recorded'
          ? 'border-[#B08D57] bg-[#F7F2E7] text-[#7A6438]'
          : 'border-[#9E3B2C] bg-[#FBF3EF] text-[#9E3B2C]';
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.05em] ${cls}`}
      title={r.detail}
    >
      {r.icon === 'check' && (
        <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" aria-hidden>
          <path d="M2 6.5 4.8 9 10 3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      {r.icon === 'demo' && (
        <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" aria-hidden>
          <rect x="3" y="1.5" width="6" height="3" rx="1" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M6 4.5v3M4 10.5h4M6 7.5v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      )}
      {r.icon === 'recorded' && (
        <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" aria-hidden>
          <rect x="2" y="6.5" width="8" height="4" rx="0.8" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path d="M4 6.5V5a2 2 0 0 1 4 0v1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      )}
      {r.icon === 'unavailable' && (
        <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" aria-hidden>
          <circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M3.2 3.2 8.8 8.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      )}
      {r.label}
    </span>
  );
}

/**
 * Data-class badge in plain language ("Real radar measurements",
 * "Demonstration data", "Real data — evidence view"). The precise product
 * class is available in the title attribute.
 */
export function DataClassBadge({ dataClass, mode }: { dataClass: 'real' | 'synthetic' | 'derived'; mode: 'quantitative' | 'evidence' }) {
  if (dataClass === 'synthetic') {
    return (
      <span
        className="inline-flex items-center rounded-full border border-[#B0714E] bg-[#F7EBE2] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[#8F4A2B]"
        title="Synthetic fixture: simulated radar data with analytic ground truth, generated for validation. Not an observation of the Earth."
      >
        Demonstration data
      </span>
    );
  }
  if (mode === 'evidence') {
    return (
      <span
        className="inline-flex items-center rounded-full border border-[#B08D57] bg-[#F7F2E7] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[#7A6438]"
        title="Real NISAR catalog records and official browse imagery; the full data product needs a NASA Earthdata login to download."
      >
        Real data · evidence view
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center rounded-full border border-[#2C5F58] bg-[#EDF4F1] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[#2C5F58]"
      title="Real satellite radar measurements from an official NISAR product."
    >
      Real radar measurements
    </span>
  );
}

const STATUS_STYLES: Record<Hypothesis['status'], { cls: string; label: string; technical: string }> = {
  supported: { cls: 'border-[#2C5F58] bg-[#2C5F58] text-white', label: 'fits the evidence', technical: 'supported' },
  consistent: { cls: 'border-[#4C7A5A] text-[#4C7A5A]', label: 'consistent with it', technical: 'consistent' },
  plausible: { cls: 'border-[#B08D57] text-[#7A6438]', label: 'possible', technical: 'plausible' },
  'insufficient-evidence': { cls: 'border-[#8A837B] text-[#6F6862]', label: 'not enough evidence', technical: 'insufficient evidence' },
  'not-supported': { cls: 'border-[#9E3B2C] text-[#9E3B2C]', label: 'not supported', technical: 'not supported' },
};

export function HypothesisStatusBadge({ status }: { status: Hypothesis['status'] }) {
  const s = STATUS_STYLES[status];
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.05em] ${s.cls}`}
      title={`Technical status: ${s.technical}`}
    >
      {s.label}
    </span>
  );
}

export function fmtDate(iso: string | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

export function fmtDateTime(iso: string | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 16).replace('T', ' ') + ' UTC';
}

export function fmtMm(m: number | undefined, digits = 1): string {
  if (m === undefined || Number.isNaN(m)) return '—';
  return `${(m * 1000).toFixed(digits)} mm`;
}

export function fmtKm2(k: number | undefined, digits = 0): string {
  if (k === undefined || Number.isNaN(k)) return '—';
  // Small areas must not round to a misleading "0 km²": show decimals instead.
  if (digits === 0 && k > 0 && k < 1) return `${k.toFixed(2)} km²`;
  if (digits === 0 && k >= 1 && k < 10) return `${k.toFixed(1)} km²`;
  return `${k.toFixed(digits)} km²`;
}
