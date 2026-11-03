"use client";

/** What the verdict was built from, in words, right on the card (not deep in the Preprocessing page):
 *  the stack and pair it used, the window really analysed, what the status means, the detection floor, the radar
 *  conventions that were assumed and any warning about incomplete input. Everything shown is read from the engine's
 *  result (detail.inputs / detail.status_meaning / evidence); nothing is computed or guessed here. */
import type { PipelineResult } from "@/lib/types";
export { verdictUseFrom, type VerdictUse } from "@/lib/verdictUse";

interface Inputs {
  mode?: string;
  selected_pair?: { stack: string; ref_time: string; sec_time: string };
  window?: { text?: string; n_pairs?: number };
  stacks?: { stacks?: { stack: string; gunw_pairs: number; analysed: boolean; drove_verdict: boolean }[]; note?: string };
  listing?: { complete?: boolean };
  reference?: { strategy?: string; edge_touch_fraction?: number | null; edge_touch_limit?: number };
}

const day = (t?: string) => (t ? t.slice(0, 10) : "?");

export default function VerdictBasis({ result }: { result: PipelineResult }) {
  const d = result.detail as { inputs?: Inputs; status_meaning?: string } | undefined;
  const inputs = d?.inputs;
  if (!inputs) return null;                                    // pipelines that do not report their inputs say nothing here
  const ev = (id: string) => (result.evidence ?? []).find((e) => e.id === id);
  const floor = ev("detection.floor"), assumed = ev("assumptions.radar"), edge = ev("reference.edge_signal");
  const sel = inputs.selected_pair;
  const incomplete = inputs.listing && inputs.listing.complete === false;
  return (
    <div className="mt-2 space-y-1 rounded border border-[#e7e2da] bg-[#faf9f6] p-2 text-[10px] text-[#4a443c]">
      {incomplete && (
        <p role="alert" className="font-semibold text-[#9e3b2c]">
          The ASF granule listing was incomplete: a quiet result cannot be trusted (see the evidence below).
        </p>
      )}
      {sel && (
        <p><b>Based on</b> stack {sel.stack}, pair {day(sel.ref_time)} → {day(sel.sec_time)}. {inputs.window?.text}.
          {" "}Gaps drawn between acquisitions elsewhere are display values, not inputs.</p>
      )}
      {inputs.stacks?.stacks && (
        <p>Stacks: {inputs.stacks.stacks.map((s) => `${s.stack} (${s.gunw_pairs} pairs${s.analysed ? ", analysed" : ", not analysed"}${s.drove_verdict ? ", drove the verdict" : ""})`).join(" · ")}. {inputs.stacks.note}</p>
      )}
      {d?.status_meaning && <p><b>{result.status.replace(/_/g, " ").toLowerCase()} means:</b> {d.status_meaning}</p>}
      {edge && <p className="text-[#9e3b2c]"><b>Edge warning:</b> {edge.claim}</p>}
      {(floor || assumed) && (
        <details>
          <summary className="cursor-pointer text-[#2c5f58]">detection floor and assumptions</summary>
          {floor && <p className="mt-1"><b>Floor:</b> {floor.claim}</p>}
          {assumed && <p className="mt-1"><b>Assumed:</b> {assumed.claim}</p>}
        </details>
      )}
    </div>
  );
}

/** How much of the engine is actually built, from the results of THIS run (A12). */
export function ModelledBanner({ results }: { results: PipelineResult[] }) {
  const built = results.filter((r) => r.status !== "NOT_MODELLED");
  const missing = results.filter((r) => r.status === "NOT_MODELLED").map((r) => r.pipeline);
  if (!missing.length) return null;
  return (
    <p role="status" className="rounded border border-[#b08d57]/40 bg-[#f7f2e7] px-2 py-1.5 text-[11px] text-[#7a6438]">
      Only {built.length} of {results.length} pipelines are built ({built.map((r) => r.pipeline).join(", ")}). The other {missing.length} report
      NOT MODELLED, so a tile can only get a verdict from the built ones: a volcano or a landslide tile cannot get a volcano/landslide verdict yet
      (volcanic_deformation: {missing.includes("volcanic_deformation") ? "not built — see docs/READINESS.md for what it needs" : "built"}).
    </p>
  );
}
