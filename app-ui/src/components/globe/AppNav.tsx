"use client";

/** AppNav (UI-build-2 §1) — the shared nav: Landing · Map · Background. Rendered by the full-bleed
 *  pages and the (site) layout, so every page offers the same three sections. `dark` switches the
 *  palette for the instrument themes (contrast pairs stay AA). `active` accepts null (pages that
 *  belong to none of the tabs, e.g. /runs/* and /verdicts/*). */
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  loadQueryContext, mapHref, retrievalHref, subscribeQueryContext, type QueryContext,
} from "@/lib/queryContext";

export type AppNavItem = "landing" | "map" | "background";

const ITEMS: { key: AppNavItem; href: string; label: string }[] = [
  { key: "landing", href: "/", label: "Landing" },
  { key: "map", href: "/map", label: "Map" },
  { key: "background", href: "/background/retrieval", label: "Background" },
];

/** The nav links are CONTEXT-AWARE: Background opens with the box/window/level the user was just
 *  looking at on the map (and Map re-opens on the pin chosen in Background), instead of the old
 *  hard-coded "/background/retrieval" that always landed on an empty form. The first render uses
 *  the plain hrefs so server and client markup match; the stored context is applied after mount. */
function useContextHrefs(): Record<AppNavItem, string> {
  const [ctx, setCtx] = useState<QueryContext>({});
  useEffect(() => {
    const read = () => setCtx(loadQueryContext());
    read();
    return subscribeQueryContext(read);
  }, []);
  return { landing: "/", map: mapHref(ctx), background: retrievalHref(ctx) };
}

export default function AppNav({ active, dark = false }: { active: AppNavItem | null; dark?: boolean }) {
  const hrefs = useContextHrefs();
  return (
    <nav aria-label="App sections" className="flex items-center gap-4">
      <Link
        href="/"
        className={`text-sm font-semibold tracking-tight ${dark ? "text-[#f2efe9]" : "text-[#1c1a17]"}`}
      >
        Dancing with SARs
        <span className={`hidden sm:inline ${dark ? "text-[#8a837b]" : "text-[#8a837b]"}`}> · nisar-hazard-engine</span>
      </Link>
      <span aria-hidden className={dark ? "text-[#3a4150]" : "text-[#e7e2da]"}>|</span>
      <ul className="flex items-center gap-3 text-xs">
        {ITEMS.map((it) => {
          const isActive = it.key === active;
          const cls = isActive
            ? dark
              ? "rounded-full bg-[#2c5f58] px-2.5 py-1 font-medium text-white"
              : "rounded-full bg-[#1c1a17] px-2.5 py-1 font-medium text-[#faf9f6]"
            : dark
              ? "text-[#aab3bd] hover:text-[#f2efe9]"
              : "text-[#8a837b] hover:text-[#2c5f58]";
          return (
            <li key={it.key}>
              <Link href={hrefs[it.key]} aria-current={isActive ? "page" : undefined} className={cls}>
                {it.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Background sub-nav (§1): Data retrieval · Preprocessing · Pipelines — rendered by the
 *  background layout so the three gated sections stay one click apart. */
export function BackgroundSubNav({ active }: { active: "retrieval" | "preprocessing" | "pipelines" }) {
  const hrefs = useContextHrefs();
  const items = [
    { key: "retrieval" as const, href: hrefs.background, label: "Data retrieval" },
    { key: "preprocessing" as const, href: "/background/preprocessing", label: "Preprocessing" },
    { key: "pipelines" as const, href: "/background/pipelines", label: "Pipelines" },
  ];
  return (
    <nav aria-label="Background sections" className="flex items-center gap-2 text-xs">
      {items.map((it) => (
        <Link
          key={it.key}
          href={it.href}
          aria-current={it.key === active ? "page" : undefined}
          className={
            it.key === active
              ? "rounded-full bg-[#2c5f58] px-2.5 py-1 font-medium text-white"
              : "rounded-full border border-[#333b47] px-2.5 py-1 text-[#aab3bd] hover:border-[#4a5462] hover:text-[#f2efe9]"
          }
        >
          {it.label}
        </Link>
      ))}
    </nav>
  );
}
