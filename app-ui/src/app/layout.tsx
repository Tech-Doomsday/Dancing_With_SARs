import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "nisar-hazard-engine",
  description:
    "One UI over the hazard pipelines: run the earthquake pipeline on an ingested scene and read the verdict, the evidence and the layers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="sticky top-0 z-50 border-b border-[#e7e2da] bg-[#faf9f6]/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
            <a href="/" className="text-sm font-semibold tracking-tight">
              Dancing with SARs <span className="text-[#8a837b]">· nisar-hazard-engine</span>
            </a>
            <span className="mono hidden text-[11px] text-[#8a837b] sm:inline">
              verdicts from the engine API — no science lives here
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        <footer className="mx-auto max-w-6xl px-4 pb-10 pt-4 text-xs text-[#8a837b]">
          Data: NISAR (JPL/ISRO) via ASF DAAC · events: USGS ComCat · scientific
          demonstration, not an operational service.
        </footer>
      </body>
    </html>
  );
}
