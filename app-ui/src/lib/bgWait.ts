"use client";

/** What the slow NASA reads are doing right now — so a wait is never silent and never ends in "gave up".
 *  bgApi.bgFetch registers every read that the engine answered with 202 (still working) and updates it on each poll.
 *  Transport only: the numbers and the error text come from the engine (attempt, last_error, retry_in_s) or from
 *  the failed poll itself; nothing is estimated here. */
import { useSyncExternalStore } from "react";

export interface BgWait {
  id: number;
  /** engine path of the read (query string dropped), e.g. /api/background/preview */
  what: string;
  /** performance.now() when the first 202 arrived */
  since: number;
  /** the engine's attempt counter: > 1 means a network failure was retried */
  attempt: number;
  lastError: string | null;
  retryInS: number | null;
  /** set while the engine itself could not be reached for a poll (tunnel blip); the read keeps running there */
  linkError: string | null;
}

let seq = 0;
let snapshot: readonly BgWait[] = [];
const listeners = new Set<() => void>();
const live = new Map<number, BgWait>();

function publish(): void {
  snapshot = Array.from(live.values());
  listeners.forEach((l) => l());
}

export function bgWaitStart(path: string): number {
  const id = ++seq;
  live.set(id, { id, what: path.split("?")[0], since: performance.now(), attempt: 0, lastError: null, retryInS: null, linkError: null });
  publish();
  return id;
}

export function bgWaitUpdate(id: number, p: { attempt?: number; last_error?: string | null; retry_in_s?: number | null }, linkError: string | null): void {
  const cur = live.get(id);
  if (!cur) return;
  live.set(id, { ...cur, attempt: p.attempt ?? cur.attempt, lastError: p.last_error ?? null, retryInS: p.retry_in_s ?? null, linkError });
  publish();
}

export function bgWaitEnd(id: number): void {
  if (live.delete(id)) publish();
}

export function useBgWaits(): readonly BgWait[] {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; },
    () => snapshot,
    () => snapshot,
  );
}
