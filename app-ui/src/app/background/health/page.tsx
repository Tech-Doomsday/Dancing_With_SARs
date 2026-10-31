"use client";

/** /background/health — environment, credentials (presence only), dependencies, import test, storage,
 *  stub-vs-implemented per stage, pipeline file state, data needs. Read live from GET /api/background/health
 *  (logic: engine/common/debug/health.py, shared with the debug hub). Network reachability is a button. */
import { useEffect, useState } from "react";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { dash } from "@/lib/explainCache";
import type { HealthPayload, NetworkCheckPayload } from "@/lib/types";
import { useGateFail, useGateRetryNonce } from "@/components/background/BackgroundGate";

type Obj = Record<string, unknown>;
const asObj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
const asArr = (v: unknown): Obj[] => (Array.isArray(v) ? (v as Obj[]) : []);
const txt = (v: unknown): string =>
  v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : dash(v as string | number);

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4 max-w-4xl">
      <h2 className="text-[11px] font-semibold text-[#f2efe9]">{title}</h2>
      <div className="mt-1 rounded border border-[#262c36] bg-[#12161c] p-2">{children}</div>
    </section>
  );
}

function KV({ o }: { o: Obj }) {
  return (
    <dl>
      {Object.entries(o).map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-b border-[#1c222b] py-0.5">
          <dt className="text-[#8b949e]">{k}</dt>
          <dd className="break-all text-right font-mono text-[#e6eaee]">{txt(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function HealthPage() {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  const [data, setData] = useState<HealthPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [net, setNet] = useState<NetworkCheckPayload | null>(null);
  const [netBusy, setNetBusy] = useState(false);
  const [netError, setNetError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setError(null);
    bg.health(ctrl.signal)
      .then((d) => !ctrl.signal.aborted && setData(d))
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [gateFail, retryNonce]);

  const runNetwork = () => {
    setNetBusy(true); setNetError(null);
    bg.healthNetwork()
      .then(setNet)
      .catch((err) => {
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setNetError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setNetBusy(false));
  };

  const h = asObj(data?.health);
  const env = asObj(h.environment);
  const deps = asArr(h.dependencies);
  const code = asArr(h.code);
  const pipes = asArr(h.pipelines);
  const imports = asArr(h.imports);
  const { creds, storage, ancillary, ...envRest } = env;

  return (
    <div className="min-h-0 flex-1 overflow-auto p-4 text-xs" aria-label="health">
      <h1 className="text-sm font-semibold text-[#f2efe9]">Health</h1>
      <p className="mt-1 max-w-3xl text-[#8b949e]">
        Facts read from this checkout and machine. Credentials show presence only; paths are scrubbed.
      </p>
      {error && <p role="alert" className="mt-3 text-[#e8a087]">{error}</p>}
      {!data && !error && <p role="status" className="mt-3 text-[#8b949e]">loading…</p>}
      {data && (
        <>
          <Section title="Environment"><KV o={envRest} /></Section>
          <Section title="Credentials (present or not)"><KV o={asObj(creds)} /></Section>
          <Section title="Storage"><KV o={asObj(storage)} /></Section>
          <Section title="Ancillary files"><KV o={asObj(ancillary)} /></Section>
          <Section title={`Dependencies (${deps.length})`}>
            <ul>{deps.map((d, i) => (
              <li key={i} className="flex justify-between border-b border-[#1c222b] py-0.5 font-mono">
                <span>{txt(d.module)}</span><span className="text-[#8b949e]">{d.installed ? txt(d.version) : "not installed"}</span>
              </li>))}</ul>
          </Section>
          <Section title={`Module import test (${imports.length})`}>
            <ul>{imports.map((d, i) => (
              <li key={i} className="flex justify-between border-b border-[#1c222b] py-0.5 font-mono">
                <span>{txt(d.module ?? d.name)}</span><span className="text-[#8b949e]">{d.ok ? "ok" : txt(d.error)}</span>
              </li>))}</ul>
          </Section>
          <Section title="Implemented vs stub, per stage">
            <ul>{code.map((g, i) => (
              <li key={i} className="flex justify-between border-b border-[#1c222b] py-0.5">
                <span>{txt(g.group)}</span><span className="font-mono text-[#8b949e]">{txt(g.implemented)} / {txt(g.total)}</span>
              </li>))}</ul>
          </Section>
          <Section title="Pipeline files (has run() = modelled)">
            <ul>{pipes.map((p, i) => (
              <li key={i} className="flex justify-between border-b border-[#1c222b] py-0.5 font-mono">
                <span>{txt(p.name)}</span><span className="text-[#8b949e]">{p.has_run ? "run() defined" : "no run()"}</span>
              </li>))}</ul>
          </Section>
          <Section title="Data needs"><pre className="overflow-auto font-mono text-[10px] text-[#cfd6dc]">{JSON.stringify(h.needs ?? null, null, 1)}</pre></Section>
          <Section title="Network reachability (on demand)">
            <button type="button" onClick={runNetwork} disabled={netBusy}
              className="rounded border border-[#333b47] px-2 py-1 text-[11px] text-[#e6eaee] disabled:opacity-50">
              {netBusy ? "checking…" : "Check NASA hosts"}
            </button>
            {netError && <p role="alert" className="mt-1 text-[#e8a087]">{netError}</p>}
            {net && (
              <ul className="mt-1">{net.hosts.map((r) => (
                <li key={r.host} className="flex justify-between border-b border-[#1c222b] py-0.5 font-mono">
                  <span>{r.host}</span><span className={r.ok ? "text-[#7fd8a6]" : "text-[#e8a087]"}>{r.ok ? txt(r.http) : txt(r.error)}</span>
                </li>))}</ul>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
