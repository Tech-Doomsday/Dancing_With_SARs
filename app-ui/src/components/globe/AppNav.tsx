"use client";

/** AppNav (§7.1) — the shared nav for the whole app: Globe · Granules · Workbench. Rendered by
 *  both full-bleed pages and the (site) layout, so URLs never change and no page forks the nav.
 *  `dark` switches the palette for the Explorer's instrument theme (contrast pairs stay AA). */
import Link from "next/link";

export type AppNavItem = "globe" | "granules" | "workbench";

const ITEMS: { key: AppNavItem; href: string; label: string }[] = [
  { key: "globe", href: "/", label: "Globe" },
  { key: "granules", href: "/granules", label: "Granules" },
  { key: "workbench", href: "/workbench", label: "Workbench" },
];

export default function AppNav({ active, dark = false }: { active: AppNavItem; dark?: boolean }) {
  return (
    <nav aria-label="App sections" className="flex items-center gap-4">
      <Link
        href="/"
        className={`text-sm font-semibold tracking-tight ${dark ? "text-[#f2efe9]" : "text-[#1c1a17]"}`}
      >
        Dancing with SARs
        <span className={dark ? "text-[#8a837b]" : "text-[#8a837b]"}> · nisar-hazard-engine</span>
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
              <Link href={it.href} aria-current={isActive ? "page" : undefined} className={cls}>
                {it.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
