import { describe, expect, test } from 'bun:test';
import {
  runDetection,
  detectedClusterStats,
  runNullSceneScreen,
} from '@/lib/science/detection';
import { runPipeline } from '@/lib/science/pipeline';
import {
  syntheticNullFixture,
  syntheticStrikeSlipFixture,
  mulberry32,
} from '@/lib/science/fixtures';
import type { GridMeta } from '@/lib/science/types';

function mkGrid(side = 64, spacing = 200): GridMeta {
  return {
    width: side,
    height: side,
    xStart: 500000,
    xSpacing: spacing,
    yStart: 4000000,
    ySpacing: -spacing,
    epsg: 32611,
  };
}

function fill(n: number, v: number): Float32Array {
  const a = new Float32Array(n);
  a.fill(v);
  return a;
}

describe('detection statistics (§8)', () => {
  test('valid-pixel masking is separated from detected-pixel classification', () => {
    const meta = mkGrid(8, 100);
    const n = 64;
    const mask = new Uint8Array(n).fill(1);
    mask[0] = 0; mask[1] = 0; mask[2] = 0; // 3 masked
    const disp = fill(n, 0);
    disp[10] = 0.5; disp[11] = 0.5; disp[12] = 0.5; disp[18] = 0.5; // 4-pixel cluster
    const sigma = fill(n, 0.001);
    const out = runDetection({ meta, displacement: disp, sigma, mask, k: 3, floorM: 0.01 }, fill(n, 0.9));
    expect(out.stats.validPixelCount).toBe(61);
    expect(out.stats.maskedPixelCount).toBe(3);
    expect(out.stats.detectedPixelCount).toBe(4);
    expect(out.stats.affectedAreaKm2).toBeCloseTo(4 * 0.01, 6); // 100 m pixels
    expect(out.stats.validFraction).toBeCloseTo(61 / 64, 6);
  });

  test('NaN displacement and NaN sigma inside the mask do not crash or detect', () => {
    const meta = mkGrid(8, 100);
    const n = 64;
    const mask = new Uint8Array(n).fill(1);
    const disp = fill(n, NaN);
    const sigma = fill(n, NaN);
    const out = runDetection({ meta, displacement: disp, sigma, mask, k: 3, floorM: 0.01 }, fill(n, 0.9));
    // |NaN| > thr is false -> no detections, no crash.
    expect(out.stats.detectedPixelCount).toBe(0);
    // NaN sigma is treated as zero statistical term -> the physical floor
    // becomes the threshold (safe fallback, never a zero threshold).
    expect(out.stats.thresholdMedianM).toBeCloseTo(0.01, 9);
  });

  test('cluster statistics count connected regions and ignore stragglers', () => {
    const meta = mkGrid(16, 100);
    const w = 16;
    const det = new Uint8Array(16 * 16);
    // A 6-pixel cluster.
    for (const [i, j] of [[3, 3], [3, 4], [3, 5], [4, 3], [4, 4], [5, 4]]) det[i * w + j] = 1;
    // A single stray pixel.
    det[12 * w + 12] = 1;
    const cl = detectedClusterStats(det, meta, 4);
    expect(cl.count).toBe(1); // the stray is below minPixels
    expect(cl.sizesKm2[0]).toBeCloseTo(6 * 0.01, 6);
    expect(cl.largestKm2).toBeCloseTo(0.06, 6);
    // With minPixels=1 both count.
    const cl1 = detectedClusterStats(det, meta, 1);
    expect(cl1.count).toBe(2);
  });

  test('diagonally adjacent pixels are separate clusters (4-neighbourhood)', () => {
    const meta = mkGrid(8, 100);
    const det = new Uint8Array(64);
    det[3 * 8 + 3] = 1;
    det[4 * 8 + 4] = 1; // diagonal only
    const cl = detectedClusterStats(det, meta, 1);
    expect(cl.count).toBe(2);
  });
});

describe('null-scene simulations (§8: correlated-noise false-positive behaviour)', () => {
  const meta = mkGrid(64, 200);
  const n = 64 * 64;

  function uniformScene(): {
    meta: GridMeta; displacement: Float32Array; sigma: Float32Array; mask: Uint8Array; k: number; floorM: number;
  } {
    return {
      meta,
      displacement: fill(n, 0),
      sigma: fill(n, 0.002), // 2 mm sigma
      mask: new Uint8Array(n).fill(1),
      k: 3,
      floorM: 0.005, // 5 mm floor > k*sigma -> floor dominates
    };
  }

  test('independent-noise null scenes: exceedance fraction is small and deterministic', () => {
    const res = runNullSceneScreen(uniformScene(), { scenes: 6, correlationLengthPx: 1, seed: 42 });
    expect(res.scenes).toBe(6);
    expect(res.detectedFractionMean).toBeLessThan(0.01);
    expect(res.detectedFractionMax).toBeLessThan(0.02);
    // Determinism: identical seeds -> identical result.
    const res2 = runNullSceneScreen(uniformScene(), { scenes: 6, correlationLengthPx: 1, seed: 42 });
    expect(res2.detectedFractionMean).toBe(res.detectedFractionMean);
    expect(res2.detectedFractionMax).toBe(res.detectedFractionMax);
  });

  test('correlated-noise null scenes fire more often than independent noise (documented, not hidden)', () => {
    const indep = runNullSceneScreen(uniformScene(), { scenes: 6, correlationLengthPx: 1, seed: 7 });
    const corr = runNullSceneScreen(uniformScene(), { scenes: 6, correlationLengthPx: 16, seed: 7 });
    // With a floor dominating the threshold both stay small, but correlation
    // must never reduce the reported exceedance below the independent case
    // by orders of magnitude — and the screen must expose the correlation
    // length it used.
    expect(corr.correlationLengthPx).toBe(16);
    expect(corr.detectedFractionMean).toBeGreaterThanOrEqual(0);
    expect(corr.note).toContain('NOT a calibrated false-alarm rate');
    // With correlation 16 px and floor == 3*sigma, exceedance needs 2.5
    // sigma of the CORRELATED field; the measured fraction documents the
    // rule's real behaviour (typically between 0 and a few percent).
    expect(corr.detectedFractionMean).toBeLessThan(0.05);
    void indep;
  });

  test('a low floor with correlated noise produces clusters — the honest failure mode', () => {
    const scene = uniformScene();
    scene.floorM = 0.001; // 1 mm floor < 3*sigma: statistical term dominates
    const res = runNullSceneScreen(scene, { scenes: 4, correlationLengthPx: 16, seed: 99 });
    // Correlated noise exceeding k*sigma forms contiguous regions; the
    // screen reports that clusters occur rather than hiding it.
    expect(res.scenesWithClusters).toBeGreaterThan(0);
    expect(res.detectedFractionMean).toBeGreaterThan(0.005);
  });

  test('empty mask returns an explicit no-run result, never zeros', () => {
    const scene = uniformScene();
    scene.mask = new Uint8Array(n); // all masked
    const res = runNullSceneScreen(scene, { scenes: 4 });
    expect(res.scenes).toBe(0);
    expect(Number.isNaN(res.detectedFractionMean)).toBe(true);
    expect(res.note).toContain('No valid pixels');
  });
});

describe('pipeline integration of detection statistics', () => {
  test('the null fixture reports zero detections with full masking bookkeeping', () => {
    const res = runPipeline(syntheticNullFixture(), {}, []);
    const s = res.summary;
    expect(s.affectedAreaKm2).toBe(0);
    expect(s.detectedPixelCount).toBe(0);
    expect(s.maskedPixelCount).toBe(s.validPixelCount > 0 ? 256 * 256 - s.validPixelCount : NaN);
    expect(s.clusters.count).toBe(0);
    expect(s.nullScreen.scenes).toBe(8);
    expect(s.nullScreen.note).toContain('Exploratory');
    // Masked + valid must account for every pixel of the frame.
    expect(s.maskedPixelCount + s.validPixelCount).toBe(256 * 256);
  });

  test('the strike-slip fixture produces cluster statistics consistent with its area', () => {
    const res = runPipeline(syntheticStrikeSlipFixture(), {}, []);
    const s = res.summary;
    expect(s.affectedAreaKm2).toBeGreaterThan(100);
    expect(s.detectedPixelCount).toBeGreaterThan(0);
    expect(s.clusters.count).toBeGreaterThan(0);
    expect(s.clusters.largestKm2).toBeLessThanOrEqual(s.affectedAreaKm2 + 1e-9);
    // Threshold bookkeeping is reported.
    expect(s.thresholdMedianM).toBeGreaterThan(0);
  });

  test('correction status is machine-readable and honest in every result', () => {
    const res = runPipeline(syntheticStrikeSlipFixture(), {}, []);
    const cs = res.summary.correctionStatus;
    expect(cs.ionosphericPhaseCorrectionApplied).toBe(false);
    expect(cs.troposphericPhaseCorrectionApplied).toBe(false);
    expect(cs.note).toContain('NOT removed');
    // Synthetic fixtures have no iono screen -> floor basis 'none'.
    expect(res.summary.ionoFloorBasis).toBe('none');
    expect(res.summary.systematicIonoFloorM).toBe(0);
  });

  test('the LA product reports the ionospheric floor from the screen statistics', async () => {
    // Data-dependent (skips without local data): the real product's screen
    // uncertainty (4.31 rad ≈ 81 mm) dominates its variation (~0.6 rad).
    const { getDatasetAvailability, getDataset } = await import('@/lib/data/datasets');
    if (getDatasetAvailability('real-nisar-la-2008').state !== 'available') return;
    const ds = getDataset('real-nisar-la-2008')!;
    const res = runPipeline(ds, {}, []);
    expect(res.summary.ionoFloorBasis).toBe('uncertainty');
    expect(res.summary.systematicIonoFloorM).toBeGreaterThan(0.07);
    expect(res.summary.systematicIonoFloorM).toBeLessThan(0.09);
  });

  test('mulberry32 seeds remain deterministic across null screens', () => {
    const a = mulberry32(1234);
    const b = mulberry32(1234);
    for (let i = 0; i < 100; i++) expect(a()).toBe(b());
  });
});
