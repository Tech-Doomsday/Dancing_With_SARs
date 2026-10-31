/** The (site) route group keeps the light chrome: sticky header, max-w-6xl main, footer. The
 *  globe lives in (globe) full-bleed and the Background tab in its own gated dark group; neither
 *  sees this layout. The header hosts the shared AppNav (Landing · Map · Background) so every
 *  page offers the same three sections. Verdict/run pages belong to no tab (active=null). */
import "./globals.css";
import AppNav from "@/components/globe/AppNav";

export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-50 border-b border-[#e7e2da] bg-[#faf9f6]/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <AppNav active={null} />
          <span className="mono hidden text-xs text-[#8a837b] sm:inline">
            verdicts from the engine API — no science lives here
          </span>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
      <footer className="mt-auto border-t border-[#e7e2da] bg-[#faf9f6]">
        <div className="mx-auto max-w-6xl px-4 pb-10 pt-4 text-xs leading-relaxed text-[#8a837b]">
          Data: NISAR (JPL/ISRO) via ASF DAAC · events: USGS ComCat · imagery: Esri World Imagery.
          Scientific demonstration, not an operational service — not for emergency decisions.
        </div>
      </footer>
    </div>
  );
}
