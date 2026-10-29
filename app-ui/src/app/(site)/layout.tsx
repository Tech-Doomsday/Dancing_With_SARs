/** The (site) route group keeps today's chrome: sticky header, max-w-6xl main, footer.
 *  The globe lives in (globe) full-bleed and does not see this layout. */
import "./globals.css";

export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="sticky top-0 z-50 border-b border-[#e7e2da] bg-[#faf9f6]/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <a href="/" className="text-sm font-semibold tracking-tight">
            Dancing with SARs <span className="text-[#8a837b]">· nisar-hazard-engine</span>
          </a>
          <nav className="flex items-center gap-4 text-xs text-[#8a837b]">
            <a href="/workbench" className="hover:text-[#2c5f58]">Workbench</a>
            <span className="mono hidden sm:inline">verdicts from the engine API — no science lives here</span>
          </nav>
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
