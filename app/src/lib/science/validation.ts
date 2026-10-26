/**
 * Stage A — input validation.
 *
 * Rejects incompatible or corrupt inputs before any scientific processing.
 * Every check produces a human-readable reason surfaced to the UI.
 */

import type { ScienceDataset, Raster } from './types';

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

function validateRaster(r: Raster | undefined, name: string, errors: string[], warnings: string[]): void {
  if (!r) {
    errors.push(`${name}: raster is missing`);
    return;
  }
  const { meta, data } = r;
  if (!Number.isInteger(meta.width) || !Number.isInteger(meta.height) || meta.width < 2 || meta.height < 2) {
    errors.push(`${name}: invalid grid dimensions ${meta.width}x${meta.height}`);
    return;
  }
  if (data.length !== meta.width * meta.height) {
    errors.push(`${name}: data length ${data.length} does not match ${meta.width}x${meta.height} grid`);
  }
  if (!Number.isFinite(meta.xSpacing) || meta.xSpacing <= 0) {
    errors.push(`${name}: x spacing must be positive (got ${meta.xSpacing})`);
  }
  if (!Number.isFinite(meta.ySpacing) || meta.ySpacing === 0) {
    errors.push(`${name}: y spacing must be non-zero (got ${meta.ySpacing})`);
  }
  if (!(meta.epsg >= 32601 && meta.epsg <= 32760)) {
    warnings.push(`${name}: EPSG ${meta.epsg} is not a WGS84 UTM code; map overlay accuracy is not guaranteed`);
  }
  let nonNaN = 0;
  let finite = 0;
  let sample = 0;
  for (let i = 0; i < data.length; i += Math.max(1, Math.floor(data.length / 10000))) {
    sample++;
    const v = data[i];
    if (!Number.isNaN(v)) {
      nonNaN++;
      if (Number.isFinite(v)) finite++;
    }
  }
  if (sample > 0 && nonNaN / sample < 0.01) {
    errors.push(`${name}: fewer than 1% of sampled values are valid (grid appears empty)`);
  }
  if (finite < nonNaN) {
    warnings.push(`${name}: contains non-finite values other than NaN fill`);
  }
}

export function validateDataset(ds: ScienceDataset): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (ds.mode === 'quantitative') {
    validateRaster(ds.phase, 'phase', errors, warnings);
    validateRaster(ds.coherence, 'coherence', errors, warnings);
    if (ds.phase && ds.coherence) {
      const a = ds.phase.meta;
      const b = ds.coherence.meta;
      if (a.width !== b.width || a.height !== b.height) {
        errors.push(`phase and coherence grids have different shapes (${a.width}x${a.height} vs ${b.width}x${b.height})`);
      } else if (Math.abs(a.xStart - b.xStart) > 1e-6 || Math.abs(a.yStart - b.yStart) > 1e-6) {
        errors.push('phase and coherence grids are not co-registered (different origins)');
      }
    }
    if (!Number.isFinite(ds.numberOfLooks) || ds.numberOfLooks < 1) {
      errors.push(`numberOfLooks must be >= 1 (got ${ds.numberOfLooks})`);
    }
    const w = ds.geometry.wavelength;
    if (!Number.isFinite(w) || w <= 0.001 || w > 1) {
      errors.push(`wavelength outside the plausible radar range 0.001-1 m (got ${w})`);
    }
    const theta = ds.geometry.incidenceAngleDeg;
    if (theta < 10 || theta > 60) {
      warnings.push(`incidence angle ${theta.toFixed(1)} deg is outside the typical 20-40 deg range for spaceborne InSAR`);
    }
  }

  if (!ds.provenance.referenceTime || !ds.provenance.secondaryTime) {
    errors.push('provenance acquisition times are missing');
  } else {
    const t0 = Date.parse(ds.provenance.referenceTime);
    const t1 = Date.parse(ds.provenance.secondaryTime);
    if (Number.isNaN(t0) || Number.isNaN(t1)) {
      errors.push('provenance acquisition times are not valid ISO-8601');
    } else if (t1 <= t0) {
      errors.push('secondary acquisition time must be after the reference acquisition');
    }
  }

  if (!ds.footprintPolygon || ds.footprintPolygon.length < 3) {
    warnings.push('footprint polygon missing or degenerate; map display may be unavailable');
  }

  if (ds.mode === 'evidence' && !ds.provenance.accessNotes) {
    warnings.push('evidence-mode dataset lacks access notes');
  }

  return { ok: errors.length === 0, errors, warnings };
}
