"use client";

/** The (background) route group: gated internal tooling on the dark instrument theme. The header
 *  hosts the shared AppNav (Landing · Map · Background) plus the Background sub-nav (§1), and the
 *  gate provider renders the token prompt when the engine answers 401 / gate-503. */
import { usePathname } from "next/navigation";
import AppNav, { BackgroundSubNav, type BackgroundSection } from "@/components/globe/AppNav";
import BackgroundGate from "@/components/background/BackgroundGate";

export default function BackgroundLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const active: BackgroundSection = pathname.startsWith("/background/overview")
    ? "overview"
    : pathname.startsWith("/background/preprocessing")
      ? "preprocessing"
      : pathname.startsWith("/background/pipelines")      // incl. /background/pipelines/[name]
        ? "pipelines"
        : pathname.startsWith("/background/health")
          ? "health"
          : "retrieval";
  return (
    <div className="explorer-root fixed inset-0 flex flex-col overflow-hidden bg-[#0e1116] text-[#d9dee4]">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[#262c36] px-4 py-2.5">
        <AppNav active="background" dark />
        <BackgroundSubNav active={active} />
        <span className="mono ml-auto hidden text-[10px] text-[#5a636e] sm:inline">
          internal — engine-gated (NISAR_BACKGROUND_TOKEN)
        </span>
      </header>
      <BackgroundGate>
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </BackgroundGate>
    </div>
  );
}
