"use client";

/** EngineLink — the nav's "which engine am I talking to" pill. Click it, paste the engine URL
 *  (e.g. the https://….trycloudflare.com address a Colab `nc.serve(...)` cell prints), press Link:
 *  the URL is health-checked, stored in localStorage and the page reloads onto it. No .env.local
 *  edit, no `npm run dev` restart. "Use default" drops the override again.
 *  Transport config only — no science. */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_ENGINE, hasEngineOverride, normalizeEngineUrl, probeEngine, resetEngineBase,
  setEngineBase, useEngineUrl, type EngineProbe,
} from "@/lib/engineUrl";

type Status = "checking" | "online" | "offline";

function shortHost(base: string): string {
  try {
    const u = new URL(base);
    const local = ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname);
    return local ? `local${u.port && u.port !== "8900" ? `:${u.port}` : ""}` : u.hostname;
  } catch {
    return base;
  }
}

export default function EngineLink({ dark = false }: { dark?: boolean }) {
  const base = useEngineUrl();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>("checking");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [probe, setProbe] = useState<EngineProbe | null>(null);
  const [pending, setPending] = useState<string | null>(null); // normalised URL that failed the check
  const [linked, setLinked] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // background status of the engine currently in use (re-runs when the URL changes)
  useEffect(() => {
    let alive = true;
    setStatus("checking");
    probeEngine(base, 5000).then((p) => {
      if (alive) setStatus(p.ok ? "online" : "offline");
    });
    return () => {
      alive = false;
    };
  }, [base]);

  // close on outside click / Escape; focus the field when opened
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const commit = useCallback((url: string) => {
    if (!setEngineBase(url)) {
      setProbe({ ok: false, message: "This browser blocks storage, so the URL can't be remembered. Set NEXT_PUBLIC_ENGINE_API in .env.local instead." });
      return;
    }
    setLinked(true);
    // a reload re-runs every page's health/pipeline fetch against the new engine
    setTimeout(() => window.location.reload(), 500);
  }, []);

  const link = useCallback(async () => {
    const n = normalizeEngineUrl(draft);
    setPending(null);
    if (!n.ok) {
      setProbe({ ok: false, message: n.error });
      return;
    }
    setBusy(true);
    setProbe(null);
    const p = await probeEngine(n.url);
    setBusy(false);
    setProbe(p);
    if (p.ok) commit(n.url);
    else setPending(n.url);
  }, [draft, commit]);

  const useDefault = useCallback(() => {
    resetEngineBase();
    setLinked(true);
    setTimeout(() => window.location.reload(), 300);
  }, []);

  const dot =
    status === "online" ? "bg-[#3fb68b]" : status === "offline" ? "bg-[#d9644a]" : "bg-[#8a837b] animate-pulse";
  const pill = dark
    ? "border-[#333b47] text-[#aab3bd] hover:border-[#4a5462] hover:text-[#f2efe9]"
    : "border-[#e7e2da] text-[#8a837b] hover:border-[#2c5f58] hover:text-[#2c5f58]";
  const panel = dark
    ? "border-[#333b47] bg-[#12161c] text-[#d9dee4]"
    : "border-[#e7e2da] bg-[#faf9f6] text-[#1c1a17]";
  const field = dark
    ? "border-[#333b47] bg-[#0e1116] text-[#f2efe9] placeholder:text-[#5a636e]"
    : "border-[#e7e2da] bg-white text-[#1c1a17] placeholder:text-[#b5aea5]";
  const muted = dark ? "text-[#8a95a1]" : "text-[#8a837b]";
  const remote = base !== DEFAULT_ENGINE || !/127\.0\.0\.1|localhost/.test(base);

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Engine connection: ${shortHost(base)}, ${status}. Click to change.`}
        title={`${base} — click to link a different engine`}
        className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${pill}`}
      >
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
        <span className="mono max-w-[9rem] truncate">{shortHost(base)}</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Link an engine"
          className={`z-[60] mt-2 w-80 max-w-[92vw] rounded-xl border p-3 text-xs shadow-xl max-sm:fixed max-sm:left-3 max-sm:right-3 max-sm:top-14 max-sm:w-auto sm:absolute sm:left-0 ${panel}`}
        >
          <p className="font-semibold">Engine</p>
          <p className={`mono mt-1 break-all ${muted}`}>
            {base}
            {hasEngineOverride() ? "  (pasted)" : "  (default)"}
          </p>

          <label htmlFor="engine-url-input" className={`mt-3 block ${muted}`}>
            Paste an engine URL — e.g. the Colab tunnel link
          </label>
          <input
            id="engine-url-input"
            ref={inputRef}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setProbe(null);
              setPending(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !busy && !linked) void link();
            }}
            placeholder="https://something.trycloudflare.com"
            spellCheck={false}
            autoComplete="off"
            autoCapitalize="off"
            className={`mono mt-1 w-full rounded-md border px-2 py-1.5 text-xs outline-none focus:border-[#2c5f58] ${field}`}
          />

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void link()}
              disabled={busy || linked || !draft.trim()}
              className="rounded-md bg-[#2c5f58] px-3 py-1.5 font-medium text-white hover:bg-[#357268] disabled:opacity-50"
            >
              {busy ? "Checking…" : "Link"}
            </button>
            {pending && !busy && !linked && (
              <button
                type="button"
                onClick={() => commit(pending)}
                className={`rounded-md border px-3 py-1.5 ${dark ? "border-[#333b47] hover:border-[#4a5462]" : "border-[#e7e2da] hover:border-[#2c5f58]"}`}
              >
                Link anyway
              </button>
            )}
            {hasEngineOverride() && !linked && (
              <button
                type="button"
                onClick={useDefault}
                className={`ml-auto underline-offset-2 hover:underline ${muted}`}
              >
                Use default
              </button>
            )}
          </div>

          <div aria-live="polite" className="mt-2 min-h-[1rem]">
            {linked && <p className="text-[#3fb68b]">Linked ✓ — reloading…</p>}
            {!linked && probe && (
              <p className={probe.ok ? "text-[#3fb68b]" : "text-[#e08a76]"}>{probe.message}</p>
            )}
          </div>

          {remote && (
            <p className={`mt-2 border-t pt-2 ${dark ? "border-[#262c36]" : "border-[#e7e2da]"} ${muted}`}>
              Anyone holding a tunnel URL can start scans on that engine&apos;s Earthdata login. Keep it
              private, and stop the Colab serve cell when you&apos;re done.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
