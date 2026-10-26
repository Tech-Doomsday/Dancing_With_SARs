/**
 * Tests for the system-plan core: SBAS inversion, PELT change points, the
 * Bayesian causal engine, the atmosphere corrections, and the full stack
 * pipeline (fixtures are deterministic).
 */
import { describe, test, expect } from 'bun:test';
import {
  buildSbasDesign,
  withIntervalDays,
  solveSbasPixel,
  coherenceWeight,
  selectLambdaLCurve,
} from '@/lib/science/sbas';
import { peltChangePoints, linearTrendR2 } from '@/lib/science/pelt';
import { classifySurfaceAnomaly, brierScore, ABSTENTION } from '@/lib/science/bayes';
import { turbulentField, removeLongWavelengthRamp } from '@/lib/science/atmosphere';
import { runSbasPipeline } from '@/lib/science/sbas-pipeline';
import { centralValleyStack, STACK_ID, quakeStepEpochIndex } from '@/lib/science/stacks';
import { gnssLosVelocityMmPerYr } from '@/lib/science/gnss';
import { centralValleyGnssNetwork } from '@/lib/science/stacks';

const MPR = 0.2360571 / (4 * Math.PI);

describe('SBAS design matrix (plan §6)', () => {
  test('B[k,j] = 1 exactly on the half-open interval [ref, sec)', () => {
    const ifgs = [
      { refIndex: 0, secIndex: 1, temporalBaselineDays: 12 },
      { refIndex: 1, secIndex: 3, temporalBaselineDays: 24 },
      { refIndex: 0, secIndex: 3, temporalBaselineDays: 36 },
    ];
    const d = buildSbasDesign(ifgs, 4);
    expect(d.m).toBe(3);
    expect(d.intervals).toBe(3);
    // ifg 0: [0,1)
    expect(d.b[0 * 3 + 0]).toBe(1);
    expect(d.b[0 * 3 + 1]).toBe(0);
    // ifg 1: [1,3)
    expect(d.b[1 * 3 + 1]).toBe(1);
    expect(d.b[1 * 3 + 2]).toBe(1);
    expect(d.b[1 * 3 + 0]).toBe(0);
    // ifg 2: [0,3)
    expect(d.b[2 * 3 + 0]).toBe(1);
    expect(d.b[2 * 3 + 1]).toBe(1);
    expect(d.b[2 * 3 + 2]).toBe(1);
  });

  test('rejects invalid epoch spans', () => {
    expect(() =>
      buildSbasDesign([{ refIndex: 2, secIndex: 1, temporalBaselineDays: 12 }], 4),
    ).toThrow();
  });
});

describe('SBAS pixel solve', () => {
  const epochs = Array.from({ length: 7 }, (_, i) => Date.parse(`2026-01-01T00:00:00Z`) + i * 12 * 86400000);
  const ifgs: Array<{ refIndex: number; secIndex: number; temporalBaselineDays: number }> = [];
  for (let e = 0; e + 1 < 7; e++) ifgs.push({ refIndex: e, secIndex: e + 1, temporalBaselineDays: 12 });
  ifgs.push({ refIndex: 0, secIndex: 2, temporalBaselineDays: 24 });
  const design = withIntervalDays(buildSbasDesign(ifgs, 7), epochs);

  test('recovers a constant secular velocity from noiseless data', () => {
    // True velocity 0.1 m/yr → per-ifg displacement = v * Δt/365.25.
    const phase: number[] = [];
    const coh: number[] = [];
    for (const ifg of ifgs) {
      const d = 0.1 * (ifg.temporalBaselineDays / 365.25);
      phase.push(d / MPR);
      coh.push(0.8);
    }
    const sol = solveSbasPixel(new Float64Array(phase), new Float64Array(coh), design, {
      lambda: 0, looks: 20, metresPerRad: MPR,
    });
    expect(sol.v).not.toBeNull();
    expect(sol.meanVelocityMPerYr).toBeCloseTo(0.1, 3);
    expect(sol.temporalCoherence).toBeGreaterThan(0.99);
  });

  test('recovers a coseismic step with the step-augmented model', async () => {
    const { solveSbasPixelStep } = await import('@/lib/science/sbas');
    // Constant velocity 0 + a 0.2 m step between epochs 3 and 4.
    const phase: number[] = [];
    const coh: number[] = [];
    for (const ifg of ifgs) {
      const step = ifg.secIndex > 3 && ifg.refIndex <= 3 ? 0.2 : 0;
      phase.push(step / MPR);
      coh.push(0.8);
    }
    const sol = solveSbasPixelStep(new Float64Array(phase), new Float64Array(coh), design, 3, {
      lambda: 1.58, looks: 20, metresPerRad: MPR,
    });
    // With the pipeline's regularization the adjacent-interval leak is
    // suppressed: the step is recovered within ~2.5% and the secular
    // velocity stays near zero.
    expect(Math.abs(sol.stepM - 0.2)).toBeLessThan(0.008);
    expect(Math.abs(sol.meanVelocityMPerYr)).toBeLessThan(0.015);
    // The cumulative series shows the full step at the right epoch.
    expect(sol.cumulative![4] - sol.cumulative![3]).toBeCloseTo(0.2, 2);
  });

  test('coherence weights are monotone in γ and bounded', () => {
    expect(coherenceWeight(0.9, 10)).toBeGreaterThan(coherenceWeight(0.5, 10));
    expect(Number.isFinite(coherenceWeight(0.999999, 10))).toBe(true);
    expect(Number.isFinite(coherenceWeight(0.0001, 10))).toBe(true);
  });

  test('missing observations do not poison the solve', () => {
    const phase: number[] = [];
    const coh: number[] = [];
    ifgs.forEach((ifg, k) => {
      const d = 0.1 * (ifg.temporalBaselineDays / 365.25);
      // Drop two interferograms entirely (NaN).
      phase.push(k === 2 || k === 5 ? NaN : d / MPR);
      coh.push(k === 2 || k === 5 ? NaN : 0.8);
    });
    const sol = solveSbasPixel(new Float64Array(phase), new Float64Array(coh), design, {
      lambda: 0, looks: 20, metresPerRad: MPR,
    });
    expect(sol.v).not.toBeNull();
    expect(sol.usedIfgs).toBe(5);
    // SEMANTICS: unobserved intervals are pinned to ~0 by the displacement
    // prior (honest interpolation), so the mean velocity underestimates by
    // exactly the unobserved fraction of time: 2 of 6 intervals dropped →
    // 0.1 × (4/6). The solver reports this via the per-epoch σ instead of
    // fabricating the missing motion.
    expect(sol.meanVelocityMPerYr).toBeCloseTo(0.1 * (4 / 6), 2);
  });

  test('L-curve selection returns a finite λ with a documented criterion', () => {
    const sample = Array.from({ length: 12 }, (_, px) => {
      const phase: number[] = [];
      const coh: number[] = [];
      ifgs.forEach((ifg) => {
        phase.push(((0.05 + px * 0.001) * (ifg.temporalBaselineDays / 365.25) + 0.002 * Math.sin(px)) / MPR);
        coh.push(0.75);
      });
      return { phaseRad: phase, coherence: coh };
    });
    const lc = selectLambdaLCurve(sample, design, { looks: 20, metresPerRad: MPR, extraSigmaM: 0.004 });
    expect(Number.isFinite(lc.lambda)).toBe(true);
    expect(lc.curve.length).toBeGreaterThan(3);
    expect(lc.criterion).toContain('Morozov');
  });
});

describe('PELT change points (plan §7)', () => {
  test('isolates a clean step at the right epoch', () => {
    const s: number[] = [];
    for (let i = 0; i < 21; i++) s.push(i >= 13 ? 0.2 : 0);
    const r = peltChangePoints(s);
    expect(r.changePoints).toContain(13);
  });

  test('does not flag a pure linear trend as transients', () => {
    const s = Array.from({ length: 21 }, (_, i) => i * 0.01);
    const r = peltChangePoints(s);
    expect(r.changePoints.length).toBe(0);
  });

  test('rejects NaN series with an error (caller must clean)', () => {
    expect(() => peltChangePoints([0, 1, NaN, 1, 0, 1, 0])).toThrow();
  });

  test('linearTrendR2 separates secular from stepped series', () => {
    const t = Array.from({ length: 21 }, (_, i) => i / 365.25 * 12);
    const secular = t.map((x) => 0.1 * x);
    const stepped = Array.from({ length: 21 }, (_, i) => (i >= 13 ? 0.2 : 0));
    expect(linearTrendR2(secular, t)).toBeGreaterThan(0.95);
    expect(linearTrendR2(stepped, t)).toBeLessThan(0.75);
  });
});

describe('Bayesian causal engine (plan §8/§13 Stage 3)', () => {
  test('broad-gradient linear anomaly → PRIMARY_CAUSE_SUBSID', () => {
    const r = classifySurfaceAnomaly(
      { gradS: 0.005, tempLinearity: 0.97, demCorr: 0.05 },
      { quake: 0.2, subsid: 0.5, slide: 0.3 },
    );
    expect(r.assignedState).toBe('PRIMARY_CAUSE_SUBSID');
    expect(r.posteriors.subsid).toBeGreaterThan(0.6);
    expect(r.entropy).toBeLessThan(0.75);
  });

  test('steep-gradient stepped anomaly with matching Okada residual → PRIMARY_CAUSE_QUAKE', () => {
    const r = classifySurfaceAnomaly(
      { gradS: 0.06, tempLinearity: 0.1, demCorr: 0 },
      { quake: 0.8, subsid: 0.15, slide: 0.05 },
      { quakeRmseM: 0.005, quakeSigmaM: 0.045, trendRmseM: 0.09, trendSigmaM: 0.03, slopeRmseM: NaN, slopeSigmaM: NaN },
    );
    expect(r.assignedState).toBe('PRIMARY_CAUSE_QUAKE');
    expect(r.posteriors.quake).toBeGreaterThan(0.9);
  });

  test('ambiguous features abstain with UNATTRIBUTED_INSUFFICIENT_EVIDENCE', () => {
    const r = classifySurfaceAnomaly(
      { gradS: 0.02, tempLinearity: 0.5, demCorr: 0 },
      { quake: 0.33, subsid: 0.34, slide: 0.33 },
    );
    expect(r.assignedState).toBe('UNATTRIBUTED_INSUFFICIENT_EVIDENCE');
    // The abstention fires through the leading-posterior rule here.
    expect(Math.max(...Object.values(r.posteriors))).toBeLessThan(ABSTENTION.minPosterior);
  });

  test('non-finite demCorr is neutral, never poisoning (regression)', () => {
    const r = classifySurfaceAnomaly(
      { gradS: 0.06, tempLinearity: 0.1, demCorr: NaN },
      { quake: 0.8, subsid: 0.15, slide: 0.05 },
    );
    expect(Number.isFinite(r.posteriors.quake)).toBe(true);
    expect(r.posteriors.quake).toBeGreaterThan(0.5);
  });

  test('Brier score: perfect predictions score 0, uniform-on-wrong ~2', () => {
    const perfect = [
      { quake: 1, subsid: 0, slide: 0 },
      { quake: 0, subsid: 1, slide: 0 },
    ];
    expect(brierScore(perfect, ['quake', 'subsid']).score).toBeCloseTo(0, 6);
    const wrong = [
      { quake: 0, subsid: 1, slide: 0 },
      { quake: 1, subsid: 0, slide: 0 },
    ];
    expect(brierScore(wrong, ['quake', 'subsid']).score).toBeCloseTo(2, 6);
  });
});

describe('Atmosphere corrections (plan §6)', () => {
  test('turbulent field has the requested RMS and sane tails', () => {
    const f = turbulentField(120, 90, 0.009, 9, 0.25, 42);
    let s = 0;
    let mx = 0;
    for (const v of f) {
      s += v * v;
      mx = Math.max(mx, Math.abs(v));
    }
    const rms = Math.sqrt(s / f.length);
    expect(rms).toBeCloseTo(0.009, 3);
    expect(mx).toBeLessThan(0.009 * 6); // no FFT-style spikes
  });

  test('ramp removal preserves a localized anomaly and kills a plane', () => {
    const w = 80;
    const h = 60;
    const n = w * h;
    const disp = new Float32Array(n);
    const mask = new Uint8Array(n).fill(1);
    const isRef = new Uint8Array(n);
    for (let i = 0; i < h; i++) {
      for (let j = 0; j < w; j++) {
        const idx = i * w + j;
        disp[idx] = 0.01 * (i / h) + 0.005 * (j / w); // pure ramp
        if (i < 6 || j > w - 6) isRef[idx] = 1;
      }
    }
    // Localized blob at the centre (should survive).
    for (let i = 25; i < 35; i++) {
      for (let j = 35; j < 45; j++) {
        disp[i * w + j] += 0.05;
      }
    }
    removeLongWavelengthRamp(disp, mask, isRef, w, 100, -100);
    // Blob survived:
    expect(disp[30 * w + 40]).toBeGreaterThan(0.04);
    // Far-field ramp residual is small:
    expect(Math.abs(disp[50 * w + 10])).toBeLessThan(0.005);
  });
});

describe('Central Valley stack fixture', () => {
  test('honours the documented NISAR instrument gap', () => {
    const stack = centralValleyStack();
    const gapStart = Date.parse('2026-07-27T00:00:00Z');
    const gapEnd = Date.parse('2026-08-10T23:59:59Z');
    for (const e of stack.epochs) {
      const t = Date.parse(e.time);
      expect(t < gapStart || t > gapEnd).toBe(true);
    }
    // One interval widens to 24 days across the gap.
    const days = stack.epochs.map((e) => Date.parse(e.time));
    let widened = 0;
    for (let i = 1; i < days.length; i++) {
      const d = (days[i] - days[i - 1]) / 86400000;
      expect(d === 12 || d === 24).toBe(true);
      if (d === 24) widened++;
    }
    expect(widened).toBeGreaterThanOrEqual(1);
  }, 30000);

  test('network: 24 interferograms over 21 epochs with redundant pairs', () => {
    const stack = centralValleyStack();
    expect(stack.epochs.length).toBe(21);
    expect(stack.ifgs.length).toBe(24);
    expect(stack.ifgs.filter((f) => f.secIndex - f.refIndex === 2).length).toBe(4);
  });

  test('quake step epoch matches the catalog event time', () => {
    const stack = centralValleyStack();
    const e = quakeStepEpochIndex(stack);
    expect(e).toBeGreaterThan(0);
    expect(Date.parse(stack.epochs[e].time)).toBeGreaterThan(Date.parse(stack.event.time) - 13 * 86400000);
    expect(Date.parse(stack.epochs[e - 1].time)).toBeLessThan(Date.parse(stack.event.time));
  });
});

describe('Full SBAS pipeline (plan §12 end-to-end)', () => {
  test('produces the full result contract with honest numbers', () => {
    const r = runSbasPipeline(STACK_ID, { coherenceThreshold: 0.5, detectionFloor: 0.02 }, []);
    expect(r.kind).toBe('sbas');
    expect(r.epochs.length).toBe(21);
    expect(r.summary.ifgCount).toBe(24);
    // Subsidence recovered at roughly the truth scale (truth: -238/-152 mm/yr
    // bowls; allow the atmosphere-limited margin).
    expect(r.summary.minVelocityMPerYr * 1000).toBeLessThan(-80);
    expect(r.summary.maxVelocityMPerYr * 1000).toBeGreaterThan(100);
    // The coseismic step is isolated and coincides with the event.
    const quakeSeries = r.keySeries.find((k) => k.id === 'quake-step');
    expect(quakeSeries).toBeDefined();
    const cp = quakeSeries!.changePoints.find(
      (c) => Math.abs(Date.parse(c.time) - Date.parse('2026-06-24T22:31:00Z')) < 26 * 86400000,
    );
    expect(cp).toBeDefined();
    expect(Math.abs(cp!.stepM)).toBeGreaterThan(0.1);
    // Bayesian engine: at least one quake-assigned and one subsidence-assigned anomaly.
    const states = r.anomalies.map((a) => a.bayes.assignedState);
    expect(states).toContain('PRIMARY_CAUSE_QUAKE');
    expect(states).toContain('PRIMARY_CAUSE_SUBSID');
    // Validation blocks exist and are finite.
    expect(r.gnss.stations.length).toBeGreaterThanOrEqual(5);
    expect(Number.isFinite(r.gnss.rmseMmPerYr)).toBe(true);
    expect(r.brier).toBeDefined();
    expect(r.brier!.score).toBeLessThan(0.4);
    expect(r.truthValidation).toBeDefined();
    // Honest synthetic labelling:
    expect(r.narrative.reliability).toContain('SYNTHETIC VALIDATION FIXTURE');
    expect(r.events[0].synthetic).toBe(true);
    // Transports are decodable-sized.
    expect(r.cumulative.grids.length).toBe(21);
    expect(r.velocity.width).toBeGreaterThan(50);
  }, 60000);
});

describe('GNSS LOS projection (plan §11)', () => {
  test('projects a purely vertical velocity onto cos(incidence)', () => {
    const los = gnssLosVelocityMmPerYr(
      { eastMmPerYr: 0, northMmPerYr: 0, upMmPerYr: -100 },
      { wavelength: 0.236, incidenceAngleDeg: 34, headingDeg: -12.5, passDirection: 'ascending', lookDirection: 'right' },
    );
    // Subsidence (ground dropping) = AWAY from the satellite = positive LOS
    // by the pipeline convention (+ = range increase).
    expect(los).toBeCloseTo(100 * Math.cos((34 * Math.PI) / 180), 1);
  });

  test('fixture network is documented as fixtures', () => {
    const net = centralValleyGnssNetwork(centralValleyStack());
    expect(net.length).toBe(10);
    for (const st of net) {
      expect(st.source).toContain('fixture');
    }
  }, 30000);
});
