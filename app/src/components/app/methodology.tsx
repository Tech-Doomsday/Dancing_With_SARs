'use client';

/**
 * Methodology drawer — the plain-language scientific documentation,
 * equations, validation summary and the reference registry.
 */

import { useEffect, useState } from 'react';
import { ExternalLink, BookOpen } from 'lucide-react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';

interface RefEntry {
  key: string;
  title: string;
  authors: string;
  year: number;
  venue: string;
  url: string;
  doi?: string;
  type: string;
  supports: string[];
  note?: string;
  checkedAt: string;
}

function Eq({ children }: { children: React.ReactNode }) {
  return (
    <code className="my-1.5 block overflow-x-auto rounded-md border border-[#EFEAE2] bg-[#FDFCF9] px-3 py-2 font-mono text-[12px] leading-5 text-[#3E3830]">
      {children}
    </code>
  );
}

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-[#EFEAE2] pt-5 first:border-0 first:pt-0">
      <h3 className="text-[13px] font-semibold text-[#1C1A17]">
        <span className="mr-2 font-mono text-[11px] text-[#B08D57]">{String(n).padStart(2, '0')}</span>
        {title}
      </h3>
      <div className="mt-2 space-y-2 text-[12.5px] leading-5 text-[#4A443C]">{children}</div>
    </section>
  );
}

export function MethodologyDrawer({ trigger }: { trigger: React.ReactNode }) {
  const [refs, setRefs] = useState<RefEntry[] | null>(null);
  const [refsFailed, setRefsFailed] = useState(false);

  useEffect(() => {
    fetch('/api/references')
      .then((r) => r.json())
      .then((d: { ok: boolean; references: RefEntry[] }) => {
        if (d.ok) setRefs(d.references);
        else setRefsFailed(true);
      })
      .catch(() => setRefsFailed(true));
  }, []);

  return (
    <Sheet>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent side="right" className="w-full overflow-hidden p-0 sm:max-w-xl">
        <SheetHeader className="border-b border-[#EFEAE2] px-6 py-4">
          <SheetTitle className="flex items-center gap-2 text-[15px] text-[#1C1A17]">
            <BookOpen className="h-4 w-4 text-[#8A837B]" aria-hidden />
            How MOTUS works
          </SheetTitle>
        </SheetHeader>
        <ScrollArea className="h-[calc(100vh-64px)]">
          <div className="space-y-5 px-6 py-5">
            <Section n={1} title="What this system does">
              <p>
                MOTUS measures how the Earth&apos;s surface moved between two radar satellite acquisitions, quantifies
                the measurement uncertainty, screens candidate physical explanations against independent evidence, and
                shows its work. It is built for the NASA Space Apps 2026 challenge &ldquo;Dancing with the SARs&rdquo;,
                on the architecture of the underlying research plan.
              </p>
              <p className="mt-2 font-medium text-[#4A443C]">Four concepts the plan keeps strictly separate:</p>
              <ul className="list-inside list-disc space-y-1">
                <li><strong>Surface change detection</strong> — backscatter/polarimetric power shifts from land-cover, vegetation or soil-moisture transitions (GCOV domain).</li>
                <li><strong>Physical deformation measurement</strong> — phase-based line-of-sight displacement between coregistered acquisitions (the GUNW/InSAR domain of this system).</li>
                <li><strong>Event detection</strong> — statistical identification of transients (steps, accelerations, coherence losses) against background noise models.</li>
                <li><strong>Causal attribution</strong> — evaluating candidate mechanisms against observed geometries and external context (seismic catalogs, geology, hydrology) — with explicit abstention when the evidence is insufficient.</li>
              </ul>
              <p className="mt-2">
                The plan&apos;s v1 API spec is implemented verbatim: <code className="rounded bg-[#F1EDE5] px-1">GET /api/v1/search</code> (bbox + dates → live CMR granule list),{' '}
                <code className="rounded bg-[#F1EDE5] px-1">POST /api/v1/velocity</code> (LOS velocity raster + metadata),{' '}
                <code className="rounded bg-[#F1EDE5] px-1">GET /api/v1/timeseries?lat=&amp;lon</code> (epoch dates, LOS displacement, σ bounds, change points) and{' '}
                <code className="rounded bg-[#F1EDE5] px-1">POST /api/v1/attribute</code> (anomaly → posteriors, assigned cause, Shannon entropy).
              </p>
            </Section>

            <Section n={2} title="From radar phase to displacement">
              <p>
                A radar satellite measures the distance to the ground along its line of sight (LOS) — the direction it
                looks from space, a mix of vertical and horizontal, not height alone. When the surface moves between two
                acquisitions, the travel distance changes and the echo&apos;s phase shifts. The NISAR SDS forms the
                interferogram as reference × conj(secondary) and flattens it with exp(−j(4π/λ)δr), so the residual
                phase decomposes as (JPL D-95677, §5.7 and §5.11.1):
              </p>
              <Eq>Δφ = (4π/λ) · (Δr_d + Δr_g + Δr_t + Δr_i + Δr_n)</Eq>
              <p>
                After the product&apos;s upstream corrections (topographic flattening, ionosphere, troposphere), the
                displacement term is recovered as:
              </p>
              <Eq>d_LOS = λ · φ / (4π) &nbsp;&nbsp;(positive = motion AWAY from the satellite)</Eq>
              <p>
                One fringe (a 2π phase cycle) corresponds to λ/2 of range change — 11.8 cm for the L band. The sign
                convention is verified against the official ATBD, not assumed: the frequently quoted
                d = −λφ/(4π) belongs to the opposite conjugation order.
              </p>
            </Section>

            <Section n={3} title="Quality and uncertainty">
              <p>
                Coherence (0–1) measures how consistently the radar signal is preserved between the two observations —
                high over stable rock, low over moving vegetation or water. Below the threshold (default 0.5) a pixel is
                masked rather than measured. For L independent looks and coherence γ, the phase noise is (Rodríguez
                &amp; Martín 1992):
              </p>
              <Eq>σ_φ = sqrt( (1 − γ²) / (2 · L · γ²) ) &nbsp;&nbsp;→&nbsp;&nbsp; σ_d = λ·σ_φ/(4π)</Eq>
              <p>
                Example (L = 119 looks, λ = 0.236 m): γ = 0.5 gives σ_d ≈ 2.1 mm; γ = 0.8 gives ≈ 1.2 mm. The
                reference region&apos;s observed scatter adds a robust (MAD-based) floor for long-wavelength residuals.
                The product&apos;s ionospheric-screen uncertainty is spatially correlated — a systematic, not per-pixel
                noise — so it raises the detection floor instead (e.g. ±81 mm on the 2008 sample product, and the UI
                says so).
              </p>
            </Section>

            <Section n={4} title="Detection">
              <p>A pixel counts as changed only when its displacement exceeds both a statistical and a physical bound:</p>
              <Eq>|d| &gt; max( k · σ_total , physical floor )&nbsp;&nbsp;(defaults k = 3, floor = 10 mm)</Eq>
            </Section>

            <Section n={5} title="Attribution">
              <p>
                Candidate explanations (earthquake, slow surface motion, atmospheric residual, unwrapping artefact, no
                change) are screened with explicit, evidence-linked rules — never a trained classifier pretending to
                know causes. When a catalogued earthquake falls inside the acquisition window, an Okada (1985) elastic
                forward model built from the USGS moment-tensor nodal plane, Wells &amp; Coppersmith (1994) area
                scaling and the Hanks &amp; Kanamori (1979) moment is compared with the measurement: amplitude ratio
                within a factor of 3 and pattern correlation r &gt; 0.25 count as consistent. When evidence is
                insufficient, MOTUS says so instead of guessing.
              </p>
            </Section>

            <Section n={6} title="Time-series stack pipeline (the research plan core)">
              <p>
                The stack machinery is real and exercised end-to-end by the automated test suite and the{' '}
                <code className="font-mono text-[11px]">/api/v1</code> plan-spec endpoints on a synthetic validation
                stack with known truth. It is deliberately <strong>not offered as a study area</strong> in the UI: a real
                24-granule GUNW stack is ≈ 60 GB of Earthdata-gated products that this deployment has not ingested, and
                listing a synthetic stack as if it were a place on Earth would misrepresent it. Twenty-one acquisitions
                on the 12-day repeat cycle (the documented 2026-07-27…08-10 NISAR instrument gap is honoured) form 24
                interferograms. The unwrapped phase of interferogram k between epochs a and b is modelled as
              </p>
              <Eq>φ_k = (4π/λ) · Σ_j B[k,j] · v_j · Δt_j</Eq>
              <p>
                with B the 0/1 design matrix and v the interval velocities, estimated by coherence-weighted least
                squares (weights w = 2Lγ²/(1−γ²), the inverse CRLB displacement variance) plus Tikhonov
                first-difference regularization whose λ is chosen on the L-curve by the degrees-of-freedom-aware
                Morozov discrepancy principle. Cumulative displacement integrates the interval velocities; temporal
                coherence γ_t = |Σ exp(i·residual)|/M gates unreliable pixels.
              </p>
              <p>Atmospheric mitigation follows the plan&apos;s decomposition φ_atm = stratified + turbulent + ionospheric:</p>
              <ul className="list-inside list-disc space-y-1">
                <li><strong>Stratified troposphere</strong> — phase–elevation regression over the two-stage stable reference, slope clamped to the physical regime (±10 mm/km of height); the ERA5/RAiDER raytracing of the plan needs CDS credentials this deployment does not hold (documented fallback).</li>
                <li><strong>Ionospheric ramp</strong> — planar fit over the stable reference (the frame-scale residual of the split-spectrum screen the product supplies but does not apply).</li>
                <li><strong>Turbulent remainder</strong> — quantified as a robust σ and enters the inversion as a variance component on every observation (the plan&apos;s §15 mitigation).</li>
              </ul>
              <p>
                Transients are isolated with PELT change-point detection on the epoch-to-epoch velocity series; pixels
                whose smooth fit fails are refit with an explicit step parameter, so coseismic displacement is separated
                from — not smeared into — the secular velocity field.
              </p>
            </Section>

            <Section n={7} title="Bayesian causal attribution (plan §8, six stages)">
              <p>
                Detected anomalies are clustered (connected components) and each cluster&apos;s feature vector — spatial
                gradient (m/km), temporal linearity (trend R²), DEM correlation, optional polarimetric shift — is
                evaluated against three forward hypotheses:
              </p>
              <Eq>H1 coseismic: L ∝ σ(10·(gradS − 0.05)) · (1 − linearity) · e^(−rmse²/2σ²) via Okada</Eq>
              <Eq>H2 compaction: L ∝ exp(−(gradS − 0.005)²/0.0002) · linearity (linear + seasonal)</Eq>
              <Eq>H3 slope: L ∝ gradS · demCorr (slope-parallel model)</Eq>
              <p>
                Context priors come from fault-trace proximity, basin boundaries and slope steepness; posteriors are
                normalized across hypotheses. The engine reports the normalized Shannon entropy and Bayes factor, and
                <strong> abstains</strong> (&ldquo;insufficient evidence&rdquo;) when entropy &gt; 0.75 or the leading
                posterior &lt; 60% — the plan&apos;s information-theoretic honesty gate.
              </p>
            </Section>

            <Section n={8} title="Validation">
              <ul className="list-inside list-disc space-y-1">
                <li>The Okada port matches the official DC3D reference implementation to &lt; 10⁻⁸ relative error over 24 benchmark geometries × 3 slip modes.</li>
                <li>Fault placement is calibrated against the 2D analytic screw-dislocation solution; slip senses (sinistral U1⁺, normal U2⁺, opening U3⁺) are verified by trace-jump tests.</li>
                <li>Synthetic earthquakes are recovered end-to-end to ≈ 2.6 mm MAE against analytic ground truth; the null fixture produces zero false alarms.</li>
                <li>The stack pipeline grades itself against known synthetic truth: velocity RMSE ~34 mm/yr, coseismic step recovered to ~98% of its amplitude at the correct epoch, GNSS LOS-projection RMSE ~33 mm/yr over 9 stations, Brier score ~0.16 vs the plan&apos;s 0.15 target.</li>
                <li>Tsunami solver: lake-at-rest (incl. wet/dry) to machine precision (≤ 3e-14 m/s), dam-break mass conservation 4.6e-15 relative, linear wave speed within 3% of √(gh), order-2 grid convergence, detection false-alarm 0/12 on synthetic controls; real-data hindcasts against archived NOAA DART records (Tōhoku 2011, Maule 2010) are recorded in docs/validation.md.</li>
                <li>Automated tests cover conventions, geodesy, validation gates, the SBAS solver, PELT change points, the Bayesian engine, and the tsunami stack (46 tests).</li>
              </ul>
              <p className="text-[11.5px] italic text-[#8A837B]">
                A passing software test is not equivalent to scientific validation — see docs/validation.md for what
                remains unvalidated.
              </p>
            </Section>

            <Section n={9} title="Tsunami capability (physics + evidence)">
              <p>
                The tsunami workflow keeps measurement, model and attribution separate, exactly like the radar side.
                Real NOAA DART bottom-pressure records are de-tided (robust local quadratic fit, MAD-scaled noise,
                5σ trigger with a documented seismic-phase mask) — a trigger is a detection, not a verdict.
              </p>
              <p className="mt-2 font-medium text-[#4A443C]">Model chain (all steps unit-tested):</p>
              <ul className="list-inside list-disc space-y-1">
                <li>Source: USGS finite-fault subfaults (or MT plane + Wells–Coppersmith scaling, labelled approximation) → Okada dislocations summed on the ETOPO1 grid.</li>
                <li>Corrections: Tanioka–Seno bathymetric-slope term <Eq>Δh = u_z + u_h·∇b</Eq>; Kajiura water-column transfer <Eq>η̂(k) = Δĥ(k)/cosh(k·d)</Eq>.</li>
                <li>Propagation: 2-D nonlinear shallow-water equations, well-balanced hydrostatic-reconstruction finite volumes (Audusse et al. 2004), HLL fluxes with dry-front speeds (George 2008), MUSCL + SSP-RK2, positivity-preserving wet/dry, Manning friction, sponge boundaries, CFL ≤ 0.45 with the 2-D sum form.</li>
                <li>Independent check: eikonal travel-time field ‖∇T‖ = 1/√(gh) by fast sweeping (Zhao 2005).</li>
              </ul>
              <p className="mt-2 font-medium text-[#4A443C]">Evidence levels (never conflated):</p>
              <ul className="list-inside list-disc space-y-1">
                <li>insufficient — no usable sensor record in range (missing ≠ absent)</li>
                <li>none-detected — sensors with coverage show nothing above 5σ</li>
                <li>possible — screening heuristic met and/or a trigger without a consistent simulation</li>
                <li>consistent — trigger(s) agree with the simulation within documented tolerances (±15 min, ×3 amplitude); buoys sample one wave field — correlated, not independent</li>
                <li>confirmed-official — only ever a citation to an authoritative external page</li>
              </ul>
              <p className="mt-2 text-[11.5px] italic text-[#8A837B]">
                Known limits (also shown inline with every result): ETOPO1 cells (~2–7 km) cannot resolve coastal
                inundation; dispersion and Coriolis are not modelled (regional approximation); no ensemble → no error
                bars; station positions are current deployments. Full details: docs/tsunami.md.
              </p>
            </Section>

            <Section n={10} title="References">
              {refs === null && !refsFailed && (
                <div className="space-y-2">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              )}
              {refsFailed && <p className="text-[12px] text-[#9E3B2C]">The reference registry could not be loaded. It is available at /api/references.</p>}
              {refs && (
                <ul className="space-y-3">
                  {refs.map((r) => (
                    <li key={r.key} className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3">
                      <a href={r.doi ? `https://doi.org/${r.doi}` : r.url} target="_blank" rel="noopener noreferrer" className="text-[12.5px] font-medium leading-4 text-[#1C1A17] hover:underline">
                        {r.title} <ExternalLink className="ml-1 inline h-3 w-3 align-[-1px] text-[#8A837B]" aria-hidden />
                      </a>
                      <div className="mt-0.5 text-[11px] text-[#8A837B]">
                        {r.authors} · {r.venue} · {r.year}
                      </div>
                      <ul className="mt-1.5 list-inside list-disc">
                        {r.supports.map((sp, i) => (
                          <li key={i} className="text-[11px] leading-4 text-[#6F6862]">{sp}</li>
                        ))}
                      </ul>
                      {r.note && <div className="mt-1 text-[10.5px] italic text-[#8A837B]">{r.note}</div>}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section n={11} title="Limitations">
              <ul className="list-inside list-disc space-y-1">
                <li>The time-series stack is a synthetic validation fixture parameterized from published Central Valley rates (real 24-granule GUNW stacks need Earthdata credentials); it validates the machinery against known truth, not the Earth.</li>
                <li>LOS-only: one viewing geometry cannot split vertical from horizontal motion.</li>
                <li>The Panama M7.7 products require NASA Earthdata credentials to download; MOTUS shows their real availability, official browse imagery and a labelled forward model instead of pretending to measure them.</li>
                <li>The 2008 sample product carries an ~81 mm ionospheric systematic floor; smaller long-wavelength signals are honestly below its detection limit.</li>
              </ul>
            </Section>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
