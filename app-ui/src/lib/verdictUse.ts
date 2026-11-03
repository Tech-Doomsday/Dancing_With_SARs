import type { PipelineResult } from "./types";

/** A pair the earthquake verdict analysed; `selected` = the one its verdict is based on (B9). */
export interface VerdictUse { stack: string; ref_time: string; sec_time: string; selected: boolean }

/** Read the analysed / selected pairs out of the engine's own result (detail.inputs); nothing is inferred. */
export function verdictUseFrom(results: PipelineResult[]): VerdictUse[] {
  const out: VerdictUse[] = [];
  for (const r of results) {
    const inp = (r.detail as { inputs?: { selected_pair?: { stack: string; ref_time: string; sec_time: string };
                                          window?: { pairs?: { stack: string; ref_time: string; sec_time: string }[] } } } | undefined)?.inputs;
    if (!inp) continue;
    const sel = inp.selected_pair;
    for (const p of inp.window?.pairs ?? [])
      out.push({ ...p, selected: !!sel && sel.stack === p.stack && sel.ref_time === p.ref_time && sel.sec_time === p.sec_time });
  }
  return out;
}
