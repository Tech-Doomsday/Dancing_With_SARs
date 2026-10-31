# 3.01 earthquake_coseismic

Sensor: NISAR (M1 GUNW interferometric stack)
Model: Okada85 elastic dislocation + consistency screen

## What this pipeline does (v1)

1. **prep** — selects the pair(s) (event-window spanning, or the newest pair in
   scan mode), applies the quality mask (finite LOS, coherence ≥ threshold,
   `conn_comp != 0`), references to the far-field median (12 % margin, with the
   degenerate-MAD → std fallback), screens cycle-jump suspects, optionally
   detrends (off by default).
2. **detection** — per-pixel `|disp| > max(k·σ_eff, 10 mm, iono floor)` with
   4-neighbour clusters (≥ 4 px) and the exploratory null-scene screen.
3. **fetch** — R1 ComCat events around the tile for the pair window (real HTTP
   adapter, injectable provider for tests). R2 ShakeMap extent is evidence-only.
4. **model** — ranks events, runs the Okada85 LOS forward screen on a strided
   subgrid (all published nodal planes), applies the amplitude/consistency
   screen, decides the status and the within-tier `raw_p` score.
5. **verdict** — returns the registry mapping with evidence, layer payloads
   (displacement / threshold / detected / residual) and the machine-readable
   `detail`.

## Conventions worth remembering

- **LOS sign is the engine's**: `los_m` and the ported pipeline are
  **+ = toward the sensor, later − earlier**. Detection classes: 1 = toward,
  2 = away. (The TypeScript app used the opposite — see docs/IMPLEMENTATION_PLAN.md §3.)
- Okada U1/U2/U3 senses (kernel frame, DC3D): +U1 sinistral, +U2 **thrust/reverse**,
  +U3 opening; the kernel's +y points **up-dip** (fault dips toward −y). The geographic
  wrappers use Aki & Richards rake (`u1 = slip·cos(rake)`, `u2 = +slip·sin(rake)`; rake
  +90 = reverse, −90 = normal) and feed the kernel the negated dip-direction coordinate.
  The kernel is pinned by the 24-row DC3D benchmark; the wrapper physics (reverse ⇒
  hanging-wall uplift, rake 0 ⇒ left-lateral) by `tests/test_okada.py`.
- Event fetch failure ⇒ `INSUFFICIENT_EVIDENCE` with the provider's error —
  never "no event".

## Tests

`python -m pytest engine/pipelines/earthquake_coseismic/tests -q` from the repo
root. `test_pipeline.py` additionally needs the engine runtime (pyproj etc.)
and exercises the real P01–P13 chain with synthetic truth.
