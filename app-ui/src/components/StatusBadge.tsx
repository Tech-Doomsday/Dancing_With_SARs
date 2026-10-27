"use client";

/** Engine status -> colour/label (docs/IMPLEMENTATION_PLAN.md 11.3). */
const STATUS_STYLES: Record<string, { cls: string; label: string }> = {
  SUPPORTED: { cls: "bg-[#2c5f58] text-white border-[#2c5f58]", label: "supported by evidence" },
  UNCATALOGUED_CANDIDATE: { cls: "border-[#2c5f58] text-[#2c5f58] bg-[#eff5f3]", label: "uncatalogued candidate" },
  NOT_SUPPORTED: { cls: "border-[#d8d2c6] bg-[#f7f4ee] text-[#4a443c]", label: "not supported by evidence" },
  INSUFFICIENT_EVIDENCE: { cls: "border-[#b08d57]/50 bg-[#f7f2e7] text-[#7a6438]", label: "not enough evidence" },
  NOT_APPLICABLE: { cls: "border-[#d8d2c6] bg-[#f7f4ee] text-[#8a837b]", label: "did not run here" },
  NOT_RUN: { cls: "border-[#d8d2c6] bg-[#f7f4ee] text-[#8a837b]", label: "not launched" },
  NO_SIGNAL: { cls: "border-[#e7e2da] bg-[#fbfaf7] text-[#8a837b]", label: "no signal here" },
  NOT_MODELLED: { cls: "border-dashed border-[#d8d2c6] bg-[#f7f4ee] text-[#8a837b]", label: "not modelled yet" },
  ERROR: { cls: "border-[#9e3b2c] bg-[#fbf3ef] text-[#9e3b2c]", label: "run failed" },
  queued: { cls: "border-[#b08d57]/50 bg-[#f7f2e7] text-[#7a6438]", label: "queued" },
  running: { cls: "border-[#b08d57]/50 bg-[#f7f2e7] text-[#7a6438]", label: "running" },
  ok: { cls: "border-[#2c5f58] bg-[#eff5f3] text-[#2c5f58]", label: "finished" },
};

export default function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? {
    cls: "border-[#d8d2c6] bg-[#f7f4ee] text-[#4a443c]",
    label: status,
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${style.cls}`}>
      {style.label}
    </span>
  );
}
