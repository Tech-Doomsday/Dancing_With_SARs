/**
 * Staged Bayesian Causal Attribution Engine (plan §8, §13 Stage 3).
 *
 * Ranks competing physical hypotheses for a detected deformation anomaly and
 * either assigns a primary cause with explicit uncertainty or ABSTAINS with
 * "UNATTRIBUTED_INSUFFICIENT_EVIDENCE" — the information-theoretic honesty
 * gate of the plan:
 *
 *   - Stage 3 features (computed upstream from the data):
 *       gradS          spatial displacement gradient (m/km across the anomaly)
 *       tempLinearity  linear-trend R² of the displacement time series
 *       demCorr        Pearson correlation (displacement, DEM elevation)
 *       polShift       polarimetric cross-ratio shift from GCOV (optional;
 *                      absent when no GCOV layer is ingested)
 *
 *   - Stage 4/5 likelihoods (plan §13 Stage 3 pseudocode, kept verbatim in
 *     form; forward-model residual modifiers from Stage 4 folded in):
 *       L(quake)  = σ(10·(gradS − 0.05)) · (1 − tempLinearity)
 *       L(subsid) = exp(−(gradS − 0.005)² / (2·0.01²)) · tempLinearity
 *       L(slide)  = gradS · demCorr
 *
 *   - Stage 5 priors P(H) come from context: proximity to mapped fault
 *     traces, hydrogeological basin boundaries, or steep slopes.
 *
 *   - Posterior P(H|D) ∝ L(H)·P(H), normalized across hypotheses.
 *
 *   - Stage 6 reporting: normalized Shannon entropy
 *         H = −Σ p log₂ p / log₂ K
 *     and Bayes factor K₁ = p_max/(1 − p_max). The engine abstains when
 *     H > 0.75 or p_max < 0.60, exactly the plan's thresholds.
 *
 * SCIENTIFIC LANGUAGE CONTRACT (inherited from the pipeline): a non-abstained
 * result is "PRIMARY_CAUSE_*" — the leading hypothesis under an explicit,
 * reproducible model — never a statement of certain causation. The Bayes
 * factor, entropy and full posterior vector are always reported alongside.
 */

import type { CausalClass, CausalFeatures, CausalPriors, ForwardModelResiduals } from './types';

/** Plan §8 Stage 6 abstention thresholds. */
export const ABSTENTION = {
  maxEntropy: 0.75,
  minPosterior: 0.6,
} as const;

/** Default prior probabilities (plan §13 Stage 3). */
export const DEFAULT_PRIORS: Record<CausalClass, number> = {
  quake: 0.2,
  subsid: 0.5,
  slide: 0.3,
};

export interface BayesianAttribution {
  /** Posterior probability per hypothesis (sums to 1 when computable). */
  posteriors: Record<CausalClass, number>;
  /** Likelihood term per hypothesis (pre-prior). */
  likelihoods: Record<CausalClass, number>;
  /** Priors used. */
  priors: Record<CausalClass, number>;
  /** Leading hypothesis by posterior. */
  maxClass: CausalClass;
  /** 'PRIMARY_CAUSE_*' or 'UNATTRIBUTED_INSUFFICIENT_EVIDENCE'. */
  assignedState: string;
  /** Normalized Shannon entropy ∈ [0,1]. */
  entropy: number;
  /** Bayes factor of the leading hypothesis: p/(1−p). */
  bayesFactor: number;
  /** Human-readable evidence items keyed to the features (for the UI). */
  evidence: string[];
  /** The exact rule that fired (assigned or abstained). */
  decisionRule: string;
}

const sigmoid = (x: number) => 1 / (1 + Math.exp(-x));

/** Plan §13 Stage 3 likelihoods, with documented forward-residual modifiers. */
export function causalLikelihoods(
  f: CausalFeatures,
  residuals?: ForwardModelResiduals,
): Record<CausalClass, number> {
  // Spatial gradient in m/km, clamped to the regime the thresholds were
  // calibrated for (plan: quake centre 0.05 m/km, subsidence centre 0.005).
  const gradS = Math.max(0, Math.min(0.5, f.gradS));
  const linearity = Math.max(0, Math.min(1, f.tempLinearity));

  // H1 coseismic: sharp gradients + non-linear (step) time behaviour.
  const pGradQuake = sigmoid(10 * (gradS - 0.05));
  let lQuake = pGradQuake * (1 - linearity);

  // H2 aquifer compaction: gentle gradients + linear secular trend.
  const pGradSubsid = Math.exp(-((gradS - 0.005) ** 2) / (2 * 0.01 ** 2));
  let lSubsid = pGradSubsid * linearity;

  // H3 slope instability: gradient magnitude × topographic correlation.
  // A non-finite demCorr (not computable — e.g. step-field anomalies) is
  // NEUTRAL (0): Math.min/max with NaN propagates NaN through the whole
  // posterior and collapses it to the degenerate uniform branch.
  const demCorr = Number.isFinite(f.demCorr) ? f.demCorr : 0;
  let lSlide = Math.max(0, gradS) * Math.max(-1, Math.min(1, demCorr));

  // Stage 4 forward-model residuals, when available, multiply the matching
  // hypothesis likelihood by exp(−rmse²/2σ²) (a Gaussian consistency term).
  if (residuals) {
    if (Number.isFinite(residuals.quakeRmseM)) {
      const s = Math.max(residuals.quakeSigmaM, 1e-3);
      lQuake *= Math.exp(-0.5 * (residuals.quakeRmseM / s) ** 2);
    }
    if (Number.isFinite(residuals.trendRmseM)) {
      const s = Math.max(residuals.trendSigmaM, 1e-3);
      lSubsid *= Math.exp(-0.5 * (residuals.trendRmseM / s) ** 2);
    }
    if (Number.isFinite(residuals.slopeRmseM)) {
      const s = Math.max(residuals.slopeSigmaM, 1e-3);
      lSlide *= Math.exp(-0.5 * (residuals.slopeRmseM / s) ** 2);
    }
  }

  // Polarimetric cross-ratio shift (plan §8 Stage 3, fourth feature): a
  // surface-change signature (moisture/land-cover) argues AGAINST a purely
  // mechanical cause; absent GCOV data leaves the term neutral at 1.
  if (f.polShift !== undefined && Number.isFinite(f.polShift) && f.polShift !== 0) {
    const w = Math.max(0, Math.min(0.5, Math.abs(f.polShift)));
    lQuake *= 1 - 0.5 * w;
    lSubsid *= 1 - 0.5 * w;
    lSlide *= 1 - 0.5 * w;
  }

  return {
    quake: Math.max(lQuake, 0),
    subsid: Math.max(lSubsid, 0),
    slide: Math.max(lSlide, 0),
  };
}

/**
 * Stage 5: Bayesian posterior over the three hypotheses given features,
 * context priors and (optional) forward-model residuals.
 */
export function classifySurfaceAnomaly(
  features: CausalFeatures,
  priorsIn?: Partial<Record<CausalClass, number>>,
  residuals?: ForwardModelResiduals,
): BayesianAttribution {
  const priors: Record<CausalClass, number> = {
    quake: priorsIn?.quake ?? DEFAULT_PRIORS.quake,
    subsid: priorsIn?.subsid ?? DEFAULT_PRIORS.subsid,
    slide: priorsIn?.slide ?? DEFAULT_PRIORS.slide,
  };
  const likelihoods = causalLikelihoods(features, residuals);

  const classes: CausalClass[] = ['quake', 'subsid', 'slide'];
  const raw: Record<CausalClass, number> = {
    quake: likelihoods.quake * priors.quake,
    subsid: likelihoods.subsid * priors.subsid,
    slide: likelihoods.slide * priors.slide,
  };
  const total = raw.quake + raw.subsid + raw.slide;
  const degenerate = !(total > 1e-12);
  const posteriors: Record<CausalClass, number> = degenerate
    ? { quake: 1 / 3, subsid: 1 / 3, slide: 1 / 3 }
    : { quake: raw.quake / total, subsid: raw.subsid / total, slide: raw.slide / total };

  // Normalized Shannon entropy (plan §8 Stage 6).
  let entropy = 0;
  if (!degenerate) {
    for (const c of classes) {
      const p = posteriors[c];
      if (p > 0) entropy -= p * Math.log2(p);
    }
    entropy /= Math.log2(classes.length);
  } else {
    entropy = 1;
  }

  let maxClass: CausalClass = 'subsid';
  for (const c of classes) if (posteriors[c] > posteriors[maxClass]) maxClass = c;
  const pMax = posteriors[maxClass];
  const assigned =
    entropy <= ABSTENTION.maxEntropy && pMax >= ABSTENTION.minPosterior
      ? `PRIMARY_CAUSE_${maxClass.toUpperCase()}`
      : 'UNATTRIBUTED_INSUFFICIENT_EVIDENCE';
  const bayesFactor = pMax / (1 - pMax + 1e-6);

  const evidence: string[] = [];
  evidence.push(
    `Spatial gradient across the anomaly: ${features.gradS.toFixed(3)} m/km ` +
      `(coseismic centre 0.05, subsidence centre 0.005 — plan §13 thresholds).`,
  );
  evidence.push(
    `Temporal linearity (linear-trend R²): ${features.tempLinearity.toFixed(2)} — ` +
      (features.tempLinearity > 0.7
        ? 'steady secular behaviour favours compaction/withdrawal processes.'
        : features.tempLinearity < 0.35
          ? 'non-linear behaviour (step or transient) favours a coseismic interpretation.'
          : 'intermediate; the time behaviour alone does not discriminate.'),
  );
  if (Number.isFinite(features.demCorr)) {
    evidence.push(
      `Displacement–elevation correlation: ${features.demCorr.toFixed(2)} — ` +
        (Math.abs(features.demCorr) > 0.5
          ? 'the signal follows topography (relevant for slope processes and stratified troposphere).'
          : 'no strong topographic control.'),
    );
  }
  if (residuals && Number.isFinite(residuals.quakeRmseM)) {
    evidence.push(
      `Okada forward-model residual: RMSE ${(residuals.quakeRmseM * 1000).toFixed(1)} mm vs ` +
        `tolerance ±${(residuals.quakeSigmaM * 1000).toFixed(1)} mm (Stage 4 consistency term).`,
    );
  } else {
    evidence.push(
      'No quantitative coseismic forward model was available for this anomaly; H1 was evaluated ' +
        'from spatial/temporal features only.',
    );
  }
  if (!Number.isFinite(features.polShift)) {
    evidence.push(
      'Polarimetric cross-ratio shift (GCOV) not ingested for this scene; the surface-change term is neutral.',
    );
  }
  evidence.push(
    `Priors from context — fault proximity ${priors.quake.toFixed(2)}, basin ${priors.subsid.toFixed(2)}, ` +
      `slope ${priors.slide.toFixed(2)} (plan §8 Stage 5: proximity to fault traces, hydrogeological basin boundaries, steep slopes).`,
  );

  const decisionRule =
    assigned === 'UNATTRIBUTED_INSUFFICIENT_EVIDENCE'
      ? `Abstained: normalized entropy ${entropy.toFixed(2)} > ${ABSTENTION.maxEntropy} ` +
        `or leading posterior ${(pMax * 100).toFixed(0)}% < ${(ABSTENTION.minPosterior * 100).toFixed(0)}% ` +
        '(plan §8 Stage 6 thresholds).'
      : `Assigned: entropy ${entropy.toFixed(2)} ≤ ${ABSTENTION.maxEntropy} and leading posterior ` +
        `${(pMax * 100).toFixed(0)}% ≥ ${(ABSTENTION.minPosterior * 100).toFixed(0)}%.`;

  return {
    posteriors,
    likelihoods,
    priors,
    maxClass,
    assignedState: assigned,
    entropy,
    bayesFactor,
    evidence,
    decisionRule,
  };
}

/**
 * Multi-class Brier Score (plan §7): BS = (1/N) Σᵢ Σₖ (pᵢₖ − oᵢₖ)²,
 * where p is the predicted posterior for hypothesis k and o the ground-truth
 * one-hot label. Lower is better; 0 = perfect; the plan's benchmark targets
 * BS ≤ 0.15 across validated subsidence zones.
 */
export function brierScore(
  predictions: Array<Record<CausalClass, number>>,
  labels: CausalClass[],
): { score: number; n: number } {
  if (predictions.length !== labels.length || predictions.length === 0) {
    return { score: NaN, n: 0 };
  }
  const classes: CausalClass[] = ['quake', 'subsid', 'slide'];
  let bs = 0;
  for (let i = 0; i < predictions.length; i++) {
    for (const c of classes) {
      const o = labels[i] === c ? 1 : 0;
      const p = Math.max(0, Math.min(1, predictions[i][c]));
      bs += (p - o) ** 2;
    }
  }
  return { score: bs / predictions.length, n: predictions.length };
}
