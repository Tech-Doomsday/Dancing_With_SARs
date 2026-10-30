/** The (site) route group keeps today's chrome: sticky header, max-w-6xl main, footer.
 *  The globe lives in (globe) full-bleed and does not see this layout. The header hosts the
 *  shared AppNav (Globe · Granules · Workbench) so every page offers the same three sections. */
import "./globals.css";
import AppNav from "@/components/globe/AppNav";

export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="sticky top-0 z-50 border-b border-[#e7e2da] bg-[#faf9f6]/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <AppNav active="workbench" />
          <span className="mono hidden text-xs text-[#8a837b] sm:inline">
            verdicts from the engine API — no science lives here
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      <footer className="mx-auto max-w-6xl px-4 pb-10 pt-4 text-xs text-[#8a837b]">
        Data: NISAR (JPL/ISRO) via ASF DAAC · events: USGS ComCat · scientific
        demonstration, not an operational service.
      </footer>
    </>
  );
}
