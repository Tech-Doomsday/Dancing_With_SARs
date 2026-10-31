/** Debounced URL write-back that can NOT bounce the user back to a page they already left.
 *
 *  The bug this replaces: a screen (Map / Retrieval) scheduled `router.replace("/map?...")` 350 ms
 *  ahead. If the user clicked a link to another page inside that window, the timer still fired and
 *  `router.replace` navigated back to the old page.
 *
 *  Rules (all enforced at FIRE time, not at schedule time):
 *    · the write only happens while location.pathname is still inside `pathPrefix`;
 *    · it uses history.replaceState(history.state, "", url) — never a router navigation — and passes
 *      the CURRENT history.state through unchanged (Next stores its own bookkeeping there; null breaks
 *      back/forward). Next >= 14.1 keeps usePathname/useSearchParams in sync with replaceState;
 *    · dispose() cancels the pending write, and a disposed writer ignores later schedule() calls;
 *    · two schedule() calls inside the window collapse into ONE write with the patches merged
 *      (later keys win; a key set to `undefined` is kept so it can clear a param).
 *
 *  Pure UI plumbing: no science, no engine data. */

/** One place for the debounce and the page prefixes (no magic numbers at call sites). */
export const URL_WRITE_DELAY_MS = 350;
export const MAP_PATH_PREFIX = "/map";
export const RETRIEVAL_PATH_PREFIX = "/background/retrieval";

/** The slice of `window` the writer needs; injectable so the unit test needs no browser. */
export interface UrlEnv {
  location: { pathname: string; search: string };
  history: { state: unknown; replaceState(data: unknown, unused: string, url?: string | null): void };
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
}

export interface UrlWriterOptions<P extends object> {
  /** the page this writer belongs to, e.g. "/map" — a write is dropped once the URL leaves it */
  pathPrefix: string;
  delayMs?: number;
  /** merge `patch` into the CURRENT query (a fresh copy, free to mutate) and return the new query */
  build: (current: URLSearchParams, patch: Partial<P>) => URLSearchParams;
  /** lazy so server rendering never touches `window`; defaults to the real window */
  env?: () => UrlEnv;
}

export interface UrlWriter<P extends object> {
  schedule(patch: Partial<P>): void;
  /** fire the pending write now (still guarded by the pathname check) */
  flush(): void;
  dispose(): void;
}

function browserEnv(): UrlEnv {
  return {
    location: window.location,
    history: window.history,
    setTimeout: (fn, ms) => window.setTimeout(fn, ms),
    clearTimeout: (id) => window.clearTimeout(id as number),
  };
}

/** `/map` matches `/map` and `/map/x`, never `/mapping`. */
export function pathInside(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(prefix.endsWith("/") ? prefix : prefix + "/");
}

export function createUrlWriter<P extends object>(opts: UrlWriterOptions<P>): UrlWriter<P> {
  const { pathPrefix, build } = opts;
  const delayMs = opts.delayMs ?? URL_WRITE_DELAY_MS;
  const getEnv = opts.env ?? browserEnv;
  let timer: unknown = null;
  let pending: Partial<P> | null = null;
  let disposed = false;

  const cancel = () => {
    if (timer !== null) {
      getEnv().clearTimeout(timer);
      timer = null;
    }
  };

  const fire = () => {
    timer = null;
    const patch = pending;
    pending = null;
    if (disposed || patch === null) return;
    const env = getEnv();
    // the user already navigated elsewhere: writing now would drag them back
    if (!pathInside(env.location.pathname, pathPrefix)) return;
    const params = build(new URLSearchParams(env.location.search), patch);
    const qs = params.toString();
    const url = env.location.pathname + (qs ? `?${qs}` : "");
    const here = env.location.pathname + env.location.search;
    if (url === here) return;                       // nothing changed: no history churn
    env.history.replaceState(env.history.state, "", url);
  };

  return {
    schedule(patch) {
      if (disposed) return;
      pending = { ...(pending ?? {}), ...patch };
      cancel();
      timer = getEnv().setTimeout(fire, delayMs);
    },
    flush() {
      if (disposed) return;
      cancel();
      fire();
    },
    dispose() {
      disposed = true;
      pending = null;
      cancel();
    },
  };
}
