/**
 * Camera choreography constants shared between the 3D stage and the DOM
 * overlays (the orbital readout). This module must never import three —
 * overlays are part of the initial bundle, the stage is not.
 *
 * All values are fractions of the pinned Earth room (0 → 1) and are kept
 * in lockstep with the unified camera track in Stage.tsx.
 */

/** camera distance to Earth's centre — drives the altitude readout */
export const EARTH_DIST: { p: number; v: number }[] = [
  { p: 0.0, v: 6.3 },
  { p: 0.24, v: 5.95 },
  { p: 0.4, v: 5.55 },
  { p: 0.57, v: 4.7 },
  { p: 0.71, v: 3.6 },
  { p: 0.87, v: 2.8 },
  { p: 1.0, v: 2.6 },
];

/** globe rotation — the planet begins to move only when you do */
export const EARTH_ROTY: { p: number; v: number }[] = [
  { p: 0.0, v: 4.4 },
  { p: 0.26, v: 4.4 },
  { p: 0.57, v: 2.05 },
  { p: 0.81, v: 1.82 },
  { p: 0.95, v: 1.75 },
  { p: 1.0, v: 1.7 },
];
