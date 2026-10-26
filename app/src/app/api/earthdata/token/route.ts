import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

/**
 * POST /api/earthdata/token — install a NASA Earthdata bearer token.
 *
 * Validation policy (learned the hard way, 2026-10-23): the ONLY authority on
 * whether a token works is the live service. Shape checks (JWT header,
 * decodable payload, uid, expiry) are reported as informational, and the
 * claims are NEVER interpreted — a token that looks like a "login session"
 * JWT can be a perfectly valid API token and vice versa.
 *
 * The live probe requests the ASF NISAR download gateway with the bearer
 * token: 303 (presign redirect) = accepted; 401 invalid_token = rejected.
 * CMR search alone is NOT a probe — it accepts anonymous requests.
 *
 * Corrupted-copy detection: when the pasted token has the SAME payload but a
 * DIFFERENT signature than the currently configured one, the response says
 * so explicitly — that is the signature of a copy-paste that lost a single
 * character (the 2026-10-23 outage).
 *
 * The token is persisted to .env (EARTHDATA_BEARER_TOKEN) and process.env;
 * it is never logged and never returned by the GET endpoint.
 */

const ENV_PATH = path.join(process.cwd(), '.env');
const KEY = 'EARTHDATA_BEARER_TOKEN';

interface TokenShape {
  ok: boolean;
  uid?: string;
  expiresAtIso?: string;
  expired?: boolean;
  note?: string;
}

function tokenShape(token: string): TokenShape {
  const parts = token.split('.');
  if (parts.length !== 3) return { ok: false, note: 'not a JWT (expected 3 dot-separated parts)' };
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf-8')) as {
      uid?: string;
      exp?: number;
    };
    const exp = typeof payload.exp === 'number' ? new Date(payload.exp * 1000) : undefined;
    return {
      ok: true,
      uid: payload.uid,
      expiresAtIso: exp?.toISOString(),
      expired: exp ? exp.getTime() < Date.now() : false,
    };
  } catch {
    return { ok: false, note: 'payload is not decodable base64 JSON' };
  }
}

async function liveProbe(token: string): Promise<{ accepted: boolean; detail: string }> {
  // Probe with a small, real NISAR granule URL on the ASF gateway. The
  // expected result for a valid token is a 303 redirect to a presigned URL
  // (we deliberately do NOT follow it and do NOT download anything).
  const probeGranule =
    'NISAR_L2_PR_GUNW_021_013_D_071_022_4000_SH_20260522T024038_20260522T024116_20260603T024038_20260603T024115_P05023_N_F_J_001';
  const url =
    `https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L2_GUNW_PROVISIONAL_V1/${probeGranule}/${probeGranule}.h5`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30000);
  try {
    const res = await fetch(url, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
      redirect: 'manual',
      signal: ctrl.signal,
    });
    if (res.status === 303 || res.status === 302 || res.status === 301) {
      return { accepted: true, detail: `ASF download gateway returned HTTP ${res.status} (presign accepted)` };
    }
    if (res.status === 401) {
      return { accepted: false, detail: 'ASF download gateway rejected the token (HTTP 401 invalid_token)' };
    }
    return {
      accepted: false,
      detail: `ASF download gateway returned HTTP ${res.status} (unexpected — treat as not accepted)`,
    };
  } catch (err) {
    return { accepted: false, detail: `live probe failed: ${(err as Error).message}` };
  } finally {
    clearTimeout(timer);
  }
}

function readEnvToken(): string {
  try {
    const raw = fs.readFileSync(ENV_PATH, 'utf-8');
    const m = new RegExp(`^${KEY}=(.+)$`, 'm').exec(raw);
    return m ? m[1].trim() : '';
  } catch {
    return '';
  }
}

function writeEnvToken(token: string): void {
  let raw = '';
  try {
    raw = fs.readFileSync(ENV_PATH, 'utf-8');
  } catch {
    // No .env yet — start one from the documented defaults (app-relative;
    // Prisma resolves the path relative to prisma/schema.prisma).
    raw = 'DATABASE_URL="file:../db/custom.db"\n';
  }
  if (new RegExp(`^${KEY}=.*$`, 'm').test(raw)) {
    raw = raw.replace(new RegExp(`^${KEY}=.*$`, 'm'), `${KEY}=${token}`);
  } else {
    raw = `${raw.replace(/\n*$/, '\n')}${KEY}=${token}\n`;
  }
  fs.writeFileSync(ENV_PATH, raw, 'utf-8');
}

export async function POST(req: Request) {
  let body: { token?: string };
  try {
    body = (await req.json()) as { token?: string };
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid JSON body.' }, { status: 400 });
  }
  const token = (body.token ?? '').trim();
  if (!token || token.length < 100) {
    return NextResponse.json({ ok: false, error: 'No token provided (expected the full Earthdata JWT).' }, { status: 400 });
  }

  const shape = tokenShape(token);
  if (!shape.ok) {
    return NextResponse.json({ ok: false, error: `The pasted value does not look like an Earthdata token: ${shape.note}` }, { status: 422 });
  }

  // Corrupted-copy detection: same payload, different signature.
  const current = readEnvToken() || process.env.EARTHDATA_BEARER_TOKEN || '';
  let corruptedCopyNote: string | undefined;
  if (current && current !== token && current.split('.')[1] === token.split('.')[1]) {
    corruptedCopyNote =
      'NOTE: this token has the SAME identity payload as the currently configured one but a DIFFERENT signature — ' +
      'the previous stored copy was corrupted (this was the cause of the 2026-10-23 outage).';
  }

  const probe = await liveProbe(token);
  if (!probe.accepted) {
    return NextResponse.json(
      {
        ok: false,
        error: `The token was not accepted by the live NASA download gateway: ${probe.detail}. ` +
          'Shape: ' + (shape.ok ? 'valid JWT structure' : 'invalid') +
          (shape.expired ? ' — WARNING: the token payload says it has EXPIRED.' : ''),
        shape,
        ...(corruptedCopyNote ? { corruptedCopyNote } : {}),
      },
      { status: 422 },
    );
  }

  // Accepted live: persist.
  try {
    writeEnvToken(token);
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Token accepted live but could not be persisted: ${(err as Error).message}` }, { status: 500 });
  }
  process.env.EARTHDATA_BEARER_TOKEN = token;

  return NextResponse.json({
    ok: true,
    message: `Token accepted by the NASA ASF download gateway (live) and saved. uid: ${shape.uid ?? 'unknown'}` +
      (shape.expiresAtIso ? ` · expires ${shape.expiresAtIso.slice(0, 10)}` : ''),
    shape,
    ...(corruptedCopyNote ? { corruptedCopyNote } : {}),
  });
}

export async function GET() {
  const token = readEnvToken() || process.env.EARTHDATA_BEARER_TOKEN || '';
  if (!token) {
    return NextResponse.json({
      ok: true,
      configured: false,
      message: 'No Earthdata token is configured on this server.',
    });
  }
  const shape = tokenShape(token);
  return NextResponse.json({
    ok: true,
    configured: true,
    // Never return the token itself.
    shape,
  });
}

export const dynamic = 'force-dynamic';
