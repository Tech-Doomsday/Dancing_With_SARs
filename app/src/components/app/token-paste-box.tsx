'use client';

/**
 * Earthdata token paste box — shown when a selected dataset is credential-
 * gated (its data needs an Earthdata-authenticated download that is not on
 * this server). The pasted token is checked LIVE against the NASA ASF
 * download gateway by /api/earthdata/token; only a token the live service
 * accepts is saved to .env. Nothing about the token's JWT claims is ever
 * interpreted locally (the 2026-10-23 lesson).
 */

import { useState } from 'react';
import { KeyRound, Loader2, CheckCircle2, AlertTriangle, ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function TokenPasteBox({ datasetTitle }: { datasetTitle: string }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setOk(null);
    setErr(null);
    try {
      const res = await fetch('/api/earthdata/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: value.trim() }),
      });
      const data = (await res.json()) as { ok: boolean; message?: string; error?: string };
      if (data.ok) {
        setOk(data.message ?? 'Token accepted and saved.');
        setValue('');
      } else {
        setErr(data.error ?? 'The token was not accepted.');
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-[#D8D2C6] bg-[#FDFCF9] p-3.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left text-[12.5px] font-semibold text-[#4A443C] hover:text-[#1C1A17]"
      >
        <KeyRound className="h-4 w-4 shrink-0 text-[#7A6438]" aria-hidden />
        Have a NASA Earthdata account? Enable the download
        <span className="ml-auto text-[11px] font-normal text-[#8A837B]">{open ? 'hide' : 'show'}</span>
      </button>
      {open && (
        <div className="mt-2.5">
          <p className="text-[11.5px] leading-[1.45rem] text-[#6F6862]">
            The data for <strong>{datasetTitle}</strong> must be downloaded from NASA with a free Earthdata Login.
            Generate a token on your{' '}
            <a
              href="https://urs.earthdata.nasa.gov/profile"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-0.5 underline decoration-[#D8D2C6] underline-offset-2 hover:text-[#1C1A17]"
            >
              Earthdata profile page <ExternalLink className="h-3 w-3" aria-hidden />
            </a>{' '}
            (bottom of the page, &ldquo;Generate Token&rdquo;), paste it below, and this server will store it for
            authenticated downloads. The token is checked against NASA&rsquo;s live download service before it is
            saved — nothing is stored unverified.
          </p>
          <div className="mt-2 flex gap-2">
            <input
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Paste the Earthdata token here"
              aria-label="Earthdata bearer token"
              className="h-9 min-w-0 flex-1 rounded-md border border-[#D8D2C6] bg-white px-2.5 font-mono text-[11px] text-[#4A443C] placeholder:text-[#B8AFA2] focus:border-[#7A6438] focus:outline-none"
              spellCheck={false}
            />
            <Button
              onClick={submit}
              disabled={busy || value.trim().length < 50}
              className="h-9 gap-1.5 rounded-md bg-[#7A6438] px-3 text-[12px] font-medium text-white hover:bg-[#8A7350] disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
              {busy ? 'Checking…' : 'Save token'}
            </Button>
          </div>
          {ok && (
            <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-4 text-[#2C5F58]">
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {ok} The server can now authenticate to NASA Earthdata. Re-fetching the granule itself is a server-side
              step — reload the page and check the dataset&rsquo;s availability.
            </p>
          )}
          {err && (
            <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-4 text-[#9E3B2C]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {err}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
