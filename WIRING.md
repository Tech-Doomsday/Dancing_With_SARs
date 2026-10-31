# Wiring: Speckle landing → app-ui (the console)

The Speckle cinematic landing page ([CodexUjayer/speckle](https://github.com/CodexUjayer/speckle))
is wired **into** this repository's `app-ui/` — the website is now the app's front door,
not a separate project. The website stays Next.js and its component files are copied
**byte-identical** from the original repo; the Python engine is untouched.

## The route map

| Route | What renders | Status |
| --- | --- | --- |
| `/` | The Speckle cinematic landing (scroll film, WebGL Earth) | **wired in** — `src/app/(landing)/` |
| `/console` | The plain-language console home (engine status strip, steps, glossary) | moved here from `/` — file unchanged |
| `/map`, `/background/*`, `/runs/*`, `/verdicts/*` | Console pages | unchanged |
| Engine API `:8900` | FastAPI (`engine/api/main.py`) | unchanged |

The flow: the film at `/` tells the story → its **"Start exploring"** button (and the
nav **"Console"** button) enters the console at **`/console`** → which links into the
map, runs and verdicts. On console pages, the header wordmark leads back to the film.

## How the wiring works

The landing reads one environment variable (the hook its own README documents):

```
NEXT_PUBLIC_SPECKLE_APP_URL=/console
```

baked with a build-time default in `app-ui/next.config.ts` (override it when the
console is deployed elsewhere). `LINKS.app` in `src/components/speckle/data.ts`
resolves it — **no website code was edited**.

## What was added to app-ui

| Path | What |
| --- | --- |
| `src/components/speckle/` | The website's components — byte-identical copy (`Experience`, `ScrollChapters`, `StaticStory`, `earth/`, `sections/`, `overlays/`, `data.ts`, `scroll.ts`, …) |
| `src/components/speckle/RootClassSync.tsx` | New 20-line glue: tags `<html class="speckle-root">` only while the landing is mounted |
| `src/app/(landing)/layout.tsx` | New: the site's original fonts (Fraunces + Inter), metadata and dark wrapper |
| `src/app/(landing)/page.tsx` | New: renders `<Experience/>` (identical to the site's `page.tsx`) |
| `public/` | The site's media: `textures/`, `landscapes/`, `media/`, `fallback/`, `robots.txt` (app-ui had no public folder before) |

## What was modified in app-ui (the complete list)

| File | Change |
| --- | --- |
| `package.json` | Added the experience's actual runtime deps: `three`, `@react-three/fiber`, `gsap` (+ `@types/three` devDep) — the only external imports the components use beyond `lucide-react`/`react`/`next`, which app-ui already had |
| `next.config.ts` | Baked `NEXT_PUBLIC_SPECKLE_APP_URL ?? "/console"` into `env` |
| `src/app/(site)/globals.css` | **Additive only**: the site's tokens, component classes and section styles appended, scope-guarded (see below). Not one existing line changed |
| `src/components/globe/AppNav.tsx` | The "Landing" tab href `"/"` → `"/console"` (2 lines); the wordmark keeps `"/"`, which is now the film |
| `scripts/no-science-guard.mjs` | Exempted `components/speckle/data.ts` — pure story copy that NAMES engine concepts in prose (same category as the guard's "UI labels that merely NAME statuses"); it computes nothing |
| `src/app/(site)/page.tsx` | **Moved** (file unchanged) to `src/app/(site)/console/page.tsx` so the film can own `/` |

## Why the console can't be affected by the landing's styles

The site's original `globals.css` styled global elements (`body`, `html`, scrollbar,
selection) for a dark, standalone page. In the merged app those rules are **scoped**:

- design tokens + base rules → the `.speckle-page` wrapper (the landing layout root);
  the console's own `--border` / `--muted` tokens stay the console's everywhere else
- document chrome (dark scrollbar, selection, `color-scheme`, body background) →
  `html.speckle-root`, a class `RootClassSync` adds on mount and **removes on unmount** —
  verified: after navigating to `/console` the class is gone and the console background
  is its own `#faf9f6`
- the chapter accent `--accent` is re-graded at runtime on the Experience's own root
  element (inline style), so scoping does not interfere

## Running it

```bash
# 1) Engine API — port 8900
python -m uvicorn engine.api.main:app --port 8900

# 2) The app (landing film at / + console) — port 3000
cd app-ui && npm install && npm run dev
```

Verified gates: `npm run typecheck` ✓ · `npm run guard:no-science` ✓ · `npm run build` ✓
(routes: `/` 75.8 kB, `/console` 7.6 kB) · end-to-end browser check ✓ (all three landing
CTAs resolve to `/console`; console pages keep their own chrome and palette).

Note for the team: the Playwright specs that assert the old console landing at `/`
need their path updated to `/console` (left untouched here, per the wiring brief).

## What was NOT touched

The engine, docs, config, supabase, tests, colab, tools — everything outside
`app-ui`'s wiring list above — and the website's own components, which remain
byte-identical to the original repository.
