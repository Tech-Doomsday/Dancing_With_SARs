/**
 * Server-side introspection of the Python nisar-hazard-engine that lives in
 * the parent of this app directory (`engine/`, `config/`, …).
 *
 * Everything reported here is read from the REAL repository at request time:
 *   - pipeline metadata comes from `engine/pipelines/<name>/manifest.yaml`
 *     (parsed with the `yaml` package — the same files the Python
 *     `engine/registry.py` loads);
 *   - "implemented" means the pipeline's `verdict.py` defines `run()` — the
 *     registry's own criterion ("a folder without a run() yields status
 *     NOT_MODELLED");
 *   - stage implementation state is derived from whether the representative
 *     module contains real definitions (the scaffold stubs are comment-only).
 *
 * Nothing is cached across requests except a tiny TTL on directory scans, so
 * editing a manifest is reflected on the next page load.
 *
 * The engine root is discovered the same way the engine discovers itself
 * (engine/common/config.py::repo_root): walk up from the working directory to
 * the first directory that looks like the engine repo (pyproject.toml +
 * engine/). When the app runs somewhere else, set ENGINE_ROOT explicitly.
 */
import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';
import type { EnginePipeline, EngineStage, EngineStatus } from './types';

/** TTL for the (cheap) directory scans: 10 s in dev, 5 min in production. */
const SCAN_TTL_MS = process.env.NODE_ENV === 'production' ? 5 * 60_000 : 10_000;

let cachedAt = 0;
let cachedRoot: string | null = null;

/**
 * Find the nisar-hazard-engine repository root.
 * Order: ENGINE_ROOT env → walk up from cwd (and from this module) looking
 * for a directory containing both `pyproject.toml` and `engine/pipelines`.
 */
export function findEngineRoot(): string | null {
  const envRoot = process.env.ENGINE_ROOT;
  if (envRoot && fs.existsSync(path.join(envRoot, 'engine', 'pipelines'))) {
    return path.resolve(envRoot);
  }
  const now = Date.now();
  if (cachedRoot !== null && now - cachedAt < SCAN_TTL_MS) return cachedRoot;

  const starts = [process.cwd(), path.resolve(__dirname, '..', '..', '..', '..')];
  for (const start of starts) {
    let dir: string | undefined = path.resolve(start);
    for (let i = 0; i < 6 && dir; i++) {
      if (
        fs.existsSync(path.join(dir, 'pyproject.toml')) &&
        fs.existsSync(path.join(dir, 'engine', 'pipelines'))
      ) {
        cachedAt = now;
        cachedRoot = dir;
        return dir;
      }
      const parent = path.dirname(dir);
      dir = parent === dir ? undefined : parent;
    }
  }
  cachedAt = now;
  cachedRoot = null;
  return null;
}

/** A Python file is a comment-only scaffold when it defines nothing. */
function pyIsStub(file: string): boolean {
  try {
    const src = fs.readFileSync(file, 'utf-8');
    return !/^\s*(def|class)\s+\w+/m.test(src);
  } catch {
    return true;
  }
}

/** The registry's implemented-test: verdict.py must define run(). */
function pipelineImplemented(pipelineDir: string): boolean {
  try {
    const src = fs.readFileSync(path.join(pipelineDir, 'verdict.py'), 'utf-8');
    return /^\s*def\s+run\s*\(/m.test(src);
  } catch {
    return false;
  }
}

interface RawManifest {
  code?: unknown;
  name?: unknown;
  sensor?: unknown;
  model_type?: unknown;
  applies?: unknown;
  uses?: unknown;
  uses_codes?: unknown;
  own_prep?: unknown;
  own_fetch?: unknown;
  fetch_codes?: unknown;
  method?: unknown;
  output?: unknown;
  min_epochs?: unknown;
  status_values?: unknown;
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}
function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
}

/** Load and validate one manifest; returns null when it is unreadable. */
function loadManifest(pipelineDir: string): EnginePipeline | null {
  const file = path.join(pipelineDir, 'manifest.yaml');
  if (!fs.existsSync(file)) return null;
  try {
    const raw = parseYaml(fs.readFileSync(file, 'utf-8')) as RawManifest | null;
    if (!raw || typeof raw !== 'object') return null;
    const folder = path.basename(pipelineDir);
    const minEpochs =
      typeof raw.min_epochs === 'number' && Number.isFinite(raw.min_epochs) ? raw.min_epochs : null;
    return {
      code: str(raw.code),
      name: str(raw.name, folder),
      sensor: str(raw.sensor),
      modelType: str(raw.model_type),
      applies: str(raw.applies),
      uses: str(raw.uses),
      usesCodes: strList(raw.uses_codes),
      ownPrep: str(raw.own_prep),
      ownFetch: str(raw.own_fetch),
      fetchCodes: strList(raw.fetch_codes),
      method: strList(raw.method),
      output: str(raw.output),
      minEpochs,
      statusValues: strList(raw.status_values),
      implemented: pipelineImplemented(pipelineDir),
    };
  } catch {
    return null;
  }
}

/** Stage implementation reality — representative module per stage. */
function engineStages(root: string): EngineStage[] {
  const rel = (p: string) => path.join(root, p);
  const implemented = (p: string) => fs.existsSync(rel(p)) && !pyIsStub(rel(p));
  return [
    {
      n: 1,
      title: 'Data retrieval',
      blurb: 'ASF/CMR granule search, NISAR byte-range clip streaming, caches, 6-hourly poller.',
      implemented: implemented('engine/retrieval/clients/nisar_client.py'),
    },
    {
      n: 2,
      title: 'Common preprocessing',
      blurb: 'The P01–P13 chain that turns granules into a frozen, shareable SceneBundle.',
      implemented: implemented('engine/common/chain.py'),
    },
    {
      n: 3,
      title: 'Hazard pipelines',
      blurb: '16 independent pipelines (routing, gating and the process pool are real; the pipelines themselves are scaffolded — see the grid below).',
      implemented: false, // honest: counted per pipeline below
    },
    {
      n: 4,
      title: 'Verdict',
      blurb: 'Collect → calibrate → link events → select → persist. Designed, not yet built.',
      implemented: implemented('engine/verdict/collect.py'),
    },
    {
      n: 5,
      title: 'Serving & UI',
      blurb: 'This web application — the earthquake-analysis module is live here today.',
      implemented: true,
    },
  ];
}

export interface EngineCatalog {
  engineRoot: string;
  pipelineCount: number;
  implementedCount: number;
  pipelines: EnginePipeline[];
  stages: EngineStage[];
}

/** Scan the engine's pipeline manifests. Returns null when no engine repo is found. */
export function loadEngineCatalog(): EngineCatalog | null {
  const root = findEngineRoot();
  if (!root) return null;
  const pipelinesDir = path.join(root, 'engine', 'pipelines');
  let entries: string[] = [];
  try {
    entries = fs
      .readdirSync(pipelinesDir, { withFileTypes: true })
      .filter((e) => e.isDirectory() && !e.name.startsWith('_') && !e.name.startsWith('.'))
      .map((e) => path.join(pipelinesDir, e.name));
  } catch {
    return null;
  }
  const pipelines = entries
    .map(loadManifest)
    .filter((p): p is EnginePipeline => p !== null)
    .sort((a, b) => a.code.localeCompare(b.code, 'en', { numeric: true }));
  return {
    engineRoot: root,
    pipelineCount: pipelines.length,
    implementedCount: pipelines.filter((p) => p.implemented).length,
    pipelines,
    stages: engineStages(root),
  };
}

/** Filesystem facts about the engine's runtime state (honest, presence-only). */
export function loadEngineStatus(): EngineStatus | null {
  const root = findEngineRoot();
  if (!root) return null;
  const dataDir = process.env.NISAR_DATA_DIR
    ? path.resolve(process.env.NISAR_DATA_DIR)
    : path.join(root, 'data');
  return {
    engineRoot: root,
    dataDir,
    dataDirExists: fs.existsSync(dataDir),
    retrievalDbExists: fs.existsSync(path.join(dataDir, 'state', 'retrieval.sqlite')),
    bundlesDirExists: fs.existsSync(path.join(dataDir, 'bundles')),
  };
}
