"""Colab driver for nisar-hazard-engine.

No engine code is modified. This module does four things:

  1. fetch_repo / install / check_env   put the repo on a Colab runtime and verify the dependency pins
  2. set_credentials / configure        Earthdata auth from Colab Secrets; the engine's own NISAR_* env
                                        overrides, sized to this machine (see configure() for each value)
  3. scan / rerun                       drive the SAME JobRunner that POST /api/scans uses
                                        (tile_at -> NisarClient -> P01..P13 -> registry -> verdicts.db)
  4. summary / show / export / serve    results inline, as files, or over the repo's FastAPI

Order matters in one place: configure() must run before the first scan()/runner() call, because the
runner and the settings read their environment once.

One-call setup:   nc.bootstrap()     unzip (if needed) -> .env -> deps -> credentials -> tuning -> logging
Credentials:      a .env at the repo root (EARTHDATA_TOKEN=...), else Colab Secrets, else a hidden prompt.
Live progress:    configure() turns on INFO logging (step start/end, MB on wire, MB/s, granule N/M, ETA) and
                  scan() also prints a status line every few seconds when logging is off.
"""
from __future__ import annotations

import base64
import copy
import getpass
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import zipfile
from pathlib import Path

CONTENT = Path("/content") if Path("/content").is_dir() else Path.home()
REPO = CONTENT / "nisar-hazard-engine"
DATA = CONTENT / "nisar_data"

_QUIET = {"NOT_MODELLED", "NOT_APPLICABLE", "BLOCKED", "PENDING"}
_HANDLE: dict = {}        # tunnel / server handles for serve()
_LOG_ON = False           # configure() installed the INFO log handler


# ----------------------------------------------------------------------------- 1. repo + deps
def in_colab() -> bool:
    return "google.colab" in sys.modules or (Path("/content").is_dir() and "COLAB_RELEASE_TAG" in os.environ)


def _enter(repo: Path) -> Path:
    repo = Path(repo).resolve()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    os.chdir(repo)
    return repo


def _unzip(zp: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zp) as z:
        z.extractall(dest)
    if not (dest / "engine").is_dir():                       # zip wrapped in a single top folder
        inner = [p for p in dest.iterdir() if p.is_dir() and (p / "engine").is_dir()]
        if len(inner) != 1:
            raise RuntimeError(f"{zp.name} does not contain engine/ at its root or one level down")
        for item in inner[0].iterdir():
            shutil.move(str(item), str(dest / item.name))
        inner[0].rmdir()


def fetch_repo(source: str = "upload", *, drive_path: str | None = None, git_url: str | None = None,
               dest: str | os.PathLike = REPO) -> Path:
    """Get the repo onto this runtime. source: 'upload' (a zip, picker opens), 'drive' (drive_path to a
    zip), or 'git' (git_url). A zip already sitting in /content (name contains 'nisar') is used first."""
    dest = Path(dest)
    if (dest / "engine").is_dir():
        print(f"repo already at {dest}")
        return _enter(dest)
    if source == "git":
        if not git_url:
            raise ValueError("source='git' needs git_url")
        subprocess.run(["git", "clone", "--depth", "1", git_url, str(dest)], check=True)
    else:
        if source == "drive":
            from google.colab import drive
            drive.mount("/content/drive")
            zp = Path(drive_path or "")
            if not zp.is_file():
                raise FileNotFoundError(f"drive_path not found: {zp}")
        else:
            found = sorted(CONTENT.glob("*nisar*.zip"))
            if found:
                zp = found[0]
                print(f"using {zp}")
            else:
                from google.colab import files
                up = files.upload()
                zp = CONTENT / next(iter(up))
        _unzip(zp, dest)
    print(f"repo ready at {dest}")
    return _enter(dest)


def install(repo: str | os.PathLike | None = None) -> None:
    """pip install the engine's own requirement files. Run BEFORE the engine is imported."""
    repo = Path(repo or REPO)
    cmd = [sys.executable, "-m", "pip", "install", "-q"]
    for name in ("requirements-retrieval.txt", "requirements-engine.txt"):
        cmd += ["-r", str(repo / name)]
    subprocess.run(cmd, check=True)


def check_env(verbose: bool = True) -> bool:
    """Verify the live kernel sees the versions the repo pins. A package that pip upgraded while an older
    copy was already imported shows up here as a mismatch -> Runtime > Restart session, re-run from install."""
    from importlib.metadata import PackageNotFoundError, version
    want = {"numpy": (1, 26), "scipy": (1, 11), "h5py": (3, 8), "aiohttp": (3, 9), "zarr": (3, 0),
            "pyproj": (3, 6), "shapely": (2, 0), "asf_search": (7, 0), "fastapi": (0, 115)}
    bad = []
    for pkg, minimum in want.items():
        try:
            have = tuple(int(x) for x in re.findall(r"\d+", version(pkg))[:2])
        except PackageNotFoundError:
            bad.append(f"{pkg}: not installed")
            continue
        if have < minimum:
            bad.append(f"{pkg}: {'.'.join(map(str, have))} < {'.'.join(map(str, minimum))}")
        mod = sys.modules.get(pkg.replace("-", "_"))
        loaded = getattr(mod, "__version__", None)
        if loaded and tuple(int(x) for x in re.findall(r"\d+", loaded)[:2]) < minimum:
            bad.append(f"{pkg}: loaded {loaded} is older than the pin; restart the session")
    py = sys.version_info
    if py < (3, 11):
        bad.append(f"python {py.major}.{py.minor} < 3.11")
    if verbose or bad:
        print("environment OK" if not bad else "PROBLEMS:\n  " + "\n  ".join(bad))
    return not bad


# ----------------------------------------------------------------------------- 2a. .env + logging
def _dotenv_path(repo: Path | None = None) -> Path:
    return Path(repo or REPO) / ".env"


def read_dotenv(repo: str | os.PathLike | None = None) -> dict:
    """KEY=VALUE pairs of <repo>/.env ('#' comments, optional quotes, optional 'export '). Empty values are
    dropped, so a template line like EARTHDATA_TOKEN= does not count as a credential. Never prints values."""
    p = _dotenv_path(Path(repo) if repo else None)
    out: dict = {}
    if not p.is_file():
        return out
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].strip()
        if k and v:
            out[k] = v
    return out


def load_env(repo: str | os.PathLike | None = None) -> list:
    """Put <repo>/.env into os.environ without overriding anything already set (the engine's own loader needs
    python-dotenv; this one does not). Returns the NAMES loaded, never the values."""
    loaded = []
    for k, v in read_dotenv(repo).items():
        if k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded


def setup_logging(level: str | None = None) -> None:
    """INFO lines from the engine (nisar.scan / nisar.retrieval) to the cell output. asf_search's own INFO dump
    (60 lines per query) stays off; its warnings still show."""
    global _LOG_ON
    lvl = (level or os.environ.get("NISAR_LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(level=lvl, stream=sys.stdout, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("asf_search").setLevel(os.environ.get("NISAR_ASF_LOG_LEVEL", "WARNING").upper())
    for noisy in ("matplotlib", "PIL", "urllib3", "asyncio", "fsspec"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _LOG_ON = True


# ----------------------------------------------------------------------------- 2. credentials + tuning
def set_credentials() -> str:
    """EARTHDATA_TOKEN from the environment, else Colab Secrets (key 'EARTHDATA_TOKEN', or
    'EARTHDATA_USERNAME' + 'EARTHDATA_PASSWORD'), else a hidden prompt. Values are never printed or written."""
    load_env()
    in_file = {k for k in read_dotenv() if k.startswith("EARTHDATA_")}
    if os.environ.get("EARTHDATA_TOKEN") or (os.environ.get("EARTHDATA_USERNAME") and os.environ.get("EARTHDATA_PASSWORD")):
        return ".env file" if in_file else "environment"

    def secret(name: str) -> str | None:
        try:
            from google.colab import userdata
            return userdata.get(name) or None
        except Exception:
            return None

    tok = secret("EARTHDATA_TOKEN")
    if tok:
        os.environ["EARTHDATA_TOKEN"] = tok
        return "colab secret (token)"
    user, pw = secret("EARTHDATA_USERNAME"), secret("EARTHDATA_PASSWORD")
    if user and pw:
        os.environ["EARTHDATA_USERNAME"], os.environ["EARTHDATA_PASSWORD"] = user, pw
        return "colab secret (username/password)"
    tok = getpass.getpass("Earthdata token (hidden). Create one at https://urs.earthdata.nasa.gov/profile: ").strip()
    if not tok:
        raise RuntimeError("no Earthdata credentials given")
    os.environ["EARTHDATA_TOKEN"] = tok
    return "prompt (token)"


def _ram_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 8.0


def configure(data_dir: str | os.PathLike = DATA, *, concurrency: int | None = None,
              overrides: dict | None = None, quiet: bool = False, log: bool = True) -> dict:
    """Set the engine's own NISAR_* overrides (config/retrieval.yaml documents each key) for this machine.

      NISAR_DATA_DIR / NISAR_DB_DIR   local SSD, not Drive: SQLite and the chunk cache need real file locking
      NISAR_ENABLE_SCANS=1            the scan route is opt-in in the engine; this is your own runtime
      net.concurrency                 24 -> 64 simultaneous range GETs (default here; pass concurrency= to change)
      net.coalesce_gap_bytes          256 KiB -> 1 MiB: fewer, larger requests; the extra gap bytes are cheap here
      decode.threads                  = CPU count (zlib inflate releases the GIL)
      cache.ram_chunks_mb             25 % of RAM for the decoded-chunk LRU
      cache.disk_chunks_gb / clips_gb sized from free disk, capped at 30 / 15 GB

    Precedence: `overrides` > NISAR_* keys written in the repo's .env > the sizing above. NISAR_DATA_DIR and
    NISAR_DB_DIR are always this runtime's local disk unless passed in `overrides` (a Windows path from a
    laptop .env would be meaningless here). log=True also turns on INFO progress logging. Returns the values."""
    data_dir = Path(data_dir)
    load_env()
    from_file = {k for k in read_dotenv() if k.startswith("NISAR_")}
    (data_dir / "db").mkdir(parents=True, exist_ok=True)
    cpu = os.cpu_count() or 2
    free_gb = shutil.disk_usage(data_dir).free / 1e9
    applied = {
        "NISAR_DATA_DIR": str(data_dir),
        "NISAR_DB_DIR": str(data_dir / "db"),
        "NISAR_ENABLE_SCANS": "1",
        "NISAR_NET_CONCURRENCY": str(int(concurrency or 64)),
        "NISAR_NET_COALESCE_GAP_BYTES": str(1 << 20),
        "NISAR_DECODE_THREADS": str(max(2, cpu)),
        "NISAR_CACHE_RAM_CHUNKS_MB": str(int(_ram_gb() * 1024 * 0.25)),
        "NISAR_CACHE_DISK_CHUNKS_GB": str(int(max(2, min(30, free_gb * 0.35)))),
        "NISAR_CACHE_CLIPS_GB": str(int(max(2, min(15, free_gb * 0.2)))),
    }
    for k in from_file - {"NISAR_DATA_DIR", "NISAR_DB_DIR"}:       # the user's own .env choice stands
        if k in applied and k not in (overrides or {}):
            applied[k] = os.environ.get(k, applied[k])
    applied.update({k: str(v) for k, v in (overrides or {}).items()})
    os.environ.update(applied)
    if log:
        setup_logging()
    if "engine.api.state" in sys.modules:                     # a runner built under old settings must not survive
        old = sys.modules["engine.api.state"].get_runner
        try:
            old().close()
        except Exception:
            pass
        old.cache_clear()
    if not quiet:
        print(f"{cpu} CPU, {_ram_gb():.1f} GB RAM, {free_gb:.0f} GB free disk")
        for k, v in applied.items():
            print(f"  {k}={v}")
    return applied


def _deps_ok() -> bool:
    try:
        return check_env(verbose=False)
    except Exception:
        return False


def bootstrap(*, zip_path: str | os.PathLike | None = None, concurrency: int | None = None,
              data_dir: str | os.PathLike = DATA, force_install: bool = False, overrides: dict | None = None) -> dict:
    """Everything before the first scan, in one call:

      repo        unzip the first /content/*nisar*.zip (or `zip_path`) if the repo is not on the runtime yet
      .env        <repo>/.env -> environment (credentials + any NISAR_* tuning); names only are printed
      deps        pip install the repo's requirement files, skipped when the live kernel already satisfies them
      credentials .env, else Colab Secrets, else a hidden prompt
      tuning      configure(): data dir on local SSD, scans enabled, concurrency, caches sized to this machine,
                  INFO progress logging

    A package that pip upgraded while an older copy was already imported cannot be fixed in-process; if
    check_env reports it, use Runtime > Restart session and run this cell again (it then skips the install)."""
    if not (REPO / "engine").is_dir():
        if zip_path:
            _unzip(Path(zip_path), REPO)
        else:
            fetch_repo("upload", dest=REPO)
    repo = _enter(REPO)
    print(f"repo: {repo}")
    env_names = load_env(repo)
    if _dotenv_path(repo).is_file():
        print(f".env: found ({len(read_dotenv(repo))} non-empty key(s): {', '.join(sorted(read_dotenv(repo))) or 'none'})")
    else:
        print(".env: not in the zip (credentials will come from Colab Secrets or a prompt)")
    if force_install or not _deps_ok():
        print("installing requirements (a minute or two)...")
        install(repo)
    ok = check_env()
    if not ok:
        print("-> Runtime > Restart session, then run this cell again.")
        return {"ok": False}
    print("credentials:", set_credentials())
    applied = configure(data_dir, concurrency=concurrency, overrides=overrides, quiet=True)
    print(f"ready: data in {applied['NISAR_DATA_DIR']}, concurrency {applied['NISAR_NET_CONCURRENCY']}, "
          f"scans {'on' if applied['NISAR_ENABLE_SCANS'] == '1' else 'OFF'}")
    return {"ok": True, "repo": str(repo), "applied": applied}


# ----------------------------------------------------------------------------- 3. run
def runner():
    """The engine's JobRunner singleton (same object the FastAPI routes use)."""
    if not (REPO / "engine").is_dir() and "engine" not in sys.modules:
        raise RuntimeError("repo not on the path: run fetch_repo() first")
    from engine.api.state import get_runner
    return get_runner()


def _status_line(prog: dict) -> str:
    """One line from the engine's live progress dict (same data the web UI shows)."""
    bits = []
    if prog.get("step_index"):
        bits.append(f"[{prog['step_index']}/{prog['step_total']}] {prog.get('stage')}")
    else:
        bits.append(str(prog.get("stage")))
    g, n = prog.get("granules") or {}, prog.get("net") or {}
    if g.get("total"):
        eta = f", ETA ~{g['eta_s'] / 60:.0f} min" if g.get("eta_s") else ""
        bits.append(f"{g.get('product') or 'granules'} {g['done']}/{g['total']}"
                    f"{'' if not g.get('failed') else ' (' + str(g['failed']) + ' failed)'}{eta}")
    if n:
        idle = n.get("idle_s")
        bits.append(f"{n.get('mb', 0):.1f} MB ({n.get('mbps_now', 0):.2f} MB/s now, {n.get('mbps_avg', 0):.2f} avg), "
                    f"{n.get('requests', 0)} req, {n.get('inflight', '?')} in flight, {n.get('retries', 0)} retries, "
                    f"last byte {'n/a' if idle is None else format(idle, '.0f') + 's'} ago")
    return " | ".join(bits)


def _wait(r, run_id: str, poll_s: float, timeout_s: float, verbose: bool, status_every_s: float = 10.0) -> dict:
    t0, last, last_status = time.monotonic(), None, 0.0
    while True:
        st = r.get_state(run_id) or {}
        prog = st.get("progress") or {}
        stage = prog.get("stage")
        if verbose and not _LOG_ON:                       # with logging on, the engine already prints all of this
            now = time.monotonic()
            if stage != last or now - last_status >= status_every_s:
                print(f"{now - t0:7.1f}s  {_status_line(prog)}")
                last, last_status = stage, now
        if st.get("status") in ("ok", "error"):
            return st
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"run {run_id} still {st.get('status')} after {timeout_s:.0f}s (stage: {stage})")
        time.sleep(poll_s)


def _net_stats(client, wall_s: float) -> dict:
    if client is None:
        return {}
    try:
        m = dict(client.io.metrics.live() if hasattr(client.io.metrics, "live") else client.io.metrics.snapshot())
        counters = {k: v for k, v in client.counters.items() if k.startswith(("chunks", "clip", "index_built", "index_disk"))}
    except Exception:
        return {}
    mb = m.get("bytes_fetched", 0) / 1e6
    return {"MB_on_wire": round(mb, 1), "MB_requested": round(m.get("bytes_useful", 0) / 1e6, 1),
            "requests": m.get("requests"), "retries": m.get("retries"), "URL_resolves": m.get("resolves"),
            "wall_s": round(wall_s, 1),
            "avg_MB_per_s_wall": round(mb / wall_s, 2) if wall_s else None, **counters}


def scan(lon: float, lat: float, as_of: str | None = None, lookback_days: float | None = None, *,
         retriever_factory=None, poll_s: float = 2.0, timeout_s: float = 3600.0, verbose: bool = True) -> dict:
    """One location scan: the 10 km MGRS tile at (lon, lat), every pipeline the registry routes to it.
    Returns the run state (same dict GET /api/runs/{id} returns) plus `_net` (download counters)."""
    r = runner()
    holder: dict = {}
    if retriever_factory is None:
        from engine.api.jobs import _default_retriever_factory
        base = _default_retriever_factory()
    else:
        base = retriever_factory

    def factory():
        holder["client"] = base()
        return holder["client"]

    t0 = time.monotonic()
    run_id = r.start_scan(lon=lon, lat=lat, as_of=as_of, lookback_days=lookback_days, retriever_factory=factory)
    if verbose:
        print(f"run {run_id}")
    state = _wait(r, run_id, poll_s, timeout_s, verbose)
    state["_net"] = _net_stats(holder.get("client"), time.monotonic() - t0)
    try:
        state["_diag"] = diagnostics(state)
    except Exception as ex:                                   # diagnostics must never hide the result
        state["_diag"] = {"error": f"{type(ex).__name__}: {ex}"}
    if verbose:
        if state["status"] == "error":
            print(f"ERROR: {state.get('error')}")
        if state["_net"]:
            print("download:", state["_net"])
        _print_diag(state["_diag"])
    return state


def diagnostics(state: dict) -> dict:
    """What P01..P13 recorded about this run's bundle: its status and every flag (search errors, missing
    stacks, ...). A scan whose pipelines all say NOT_RUN usually has the reason here (bad token, no
    granules in the window, ASF unreachable)."""
    bid = state.get("bundle_id")
    if not bid:
        return {}
    from engine.common.bundle_io import load_bundle
    b = load_bundle(runner().bundles_dir / bid, mmap=True)
    return {"bundle_status": b.status,
            "flags": [{"severity": f.severity, "code": f.code, "step": f.step, "message": f.message} for f in b.flags]}


def _print_diag(diag: dict) -> None:
    flags = diag.get("flags") or []
    errs = [f for f in flags if f["severity"] == "error"]
    if diag.get("bundle_status") == "empty" or errs:
        print(f"bundle status: {diag.get('bundle_status')}")
    for f in errs[:6]:
        print(f"  [{f['step']}] {f['code']}: {f['message'][:260]}")
    if diag.get("bundle_status") == "empty" and not errs:
        print("  no granules resolved for this tile and window (no error recorded): try a longer lookback_days")
    other = len(flags) - len(errs)
    if other:
        print(f"  ({other} non-error flag(s); see nc.diagnostics(state))")


def rerun(bundle_id: str, pipelines: list[str], params: dict | None = None, *,
          poll_s: float = 1.0, timeout_s: float = 3600.0, verbose: bool = True) -> dict:
    """Re-run pipelines on a bundle already built by a scan (no retrieval, no download).
    params keys: coherence_threshold, significance_k, detection_floor_m, far_field_margin."""
    r = runner()
    run_id = r.start_run(bundle_id, pipelines, params)
    return _wait(r, run_id, poll_s, timeout_s, verbose)


def scan_many(points: list[tuple], **kw) -> list[dict]:
    """Sequential scans (the runner is single-flight by design: several-hundred-MB arrays per run).
    points: [(lon, lat), ...] or [(lon, lat, as_of), ...]."""
    out = []
    for p in points:
        lon, lat, *rest = p
        print(f"--- scan {lon}, {lat}")
        out.append(scan(lon, lat, rest[0] if rest else None, **kw))
    return out


# ----------------------------------------------------------------------------- 4. results
def summary(state: dict):
    """pandas DataFrame: one row per pipeline, findings first, 'nothing to say' statuses last."""
    import pandas as pd
    rows = []
    for r in state.get("results", []):
        rows.append({"pipeline": r.get("pipeline"), "status": r.get("status"), "tier": r.get("tier"),
                     "p": r.get("raw_p"), "confidence": r.get("confidence"),
                     "summary": (r.get("summary") or "")[:160]})
    df = pd.DataFrame(rows, columns=["pipeline", "status", "tier", "p", "confidence", "summary"])
    if len(df):
        df = df.assign(_q=df["status"].isin(_QUIET)).sort_values(["_q", "pipeline"]).drop(columns="_q")
    return df.reset_index(drop=True)


def decode_layer(p: dict):
    """LayerPayload -> numpy array (row 0 = north; NaN = no data for float layers)."""
    import numpy as np
    a = np.frombuffer(base64.b64decode(p["data_b64"]), dtype=p["dtype"]).reshape(p["shape"])
    return a.astype("float32") if p["dtype"] == "<f4" else a.copy()


def _plot_layer(ax, p: dict, title: str):
    import numpy as np
    from matplotlib.colors import ListedColormap
    a = decode_layer(p)
    b = p.get("bounds_lonlat")
    extent = [b[0], b[2], b[1], b[3]] if b else None
    cm = (p.get("colormap") or "sequential")
    if p["dtype"] == "<u1":
        cats = p.get("categories") or []
        tone = {"positive": "#2a9d8f", "negative": "#c1121f", "neutral": "#8d99ae"}
        vals = sorted({int(c["value"]) for c in cats} | {int(v) for v in np.unique(a)})
        cmap = ListedColormap([next((tone.get(c.get("tone"), "#8d99ae") for c in cats if int(c["value"]) == v), "#cccccc")
                               for v in vals])
        idx = np.searchsorted(vals, a)
        im = ax.imshow(idx, extent=extent, origin="upper", cmap=cmap, vmin=-0.5, vmax=len(vals) - 0.5, interpolation="nearest")
        cb = ax.figure.colorbar(im, ax=ax, ticks=range(len(vals)), fraction=0.046)
        lab = {int(c["value"]): c["label"] for c in cats}
        cb.ax.set_yticklabels([lab.get(v, str(v)) for v in vals])
    else:
        cmap = "RdBu_r" if cm.startswith("diverging") else "viridis"
        im = ax.imshow(a, extent=extent, origin="upper", cmap=cmap, vmin=p.get("vmin"), vmax=p.get("vmax"))
        ax.figure.colorbar(im, ax=ax, fraction=0.046)
    units = (p.get("units") or "").strip()
    ax.set_title(title + (("\n" + textwrap.shorten(units, 52, placeholder="...")) if units else ""), fontsize=8)
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.tick_params(labelsize=7)


def show(state: dict, *, pipelines: list[str] | None = None, max_per_pipeline: int = 4,
         save_dir: str | os.PathLike | None = None) -> None:
    """Print the summary table, then plot each pipeline's evidence layers."""
    import matplotlib.pyplot as plt
    from IPython.display import display
    display(summary(state))
    if state.get("status") == "error":
        print("run failed:", state.get("error"))
        return
    for r in state.get("results", []):
        name = r.get("pipeline")
        if pipelines and name not in pipelines:
            continue
        layers = list(((r.get("detail") or {}).get("layers") or {}).items())[:max_per_pipeline]
        if not layers:
            continue
        fig, axes = plt.subplots(1, len(layers), figsize=(4.6 * len(layers), 4.2), squeeze=False)
        for ax, (lname, p) in zip(axes[0], layers):
            if not p.get("data_b64"):
                ax.set_axis_off()
                continue
            _plot_layer(ax, p, f"{name} / {lname}")
        fig.suptitle(f"{name}: {r.get('status')}", fontsize=10)
        fig.tight_layout()
        if save_dir:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
            fig.savefig(Path(save_dir) / f"{name}.png", dpi=130)
        plt.show()


def export(state: dict, out_dir: str | os.PathLike = CONTENT / "nisar_out") -> Path:
    """results (run.json), every layer as .npy with its bounds, a PNG per pipeline -> one zip. Returns the zip."""
    import numpy as np
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    results = []
    for r in state.get("results", []):
        r = copy.deepcopy(r)
        for lname, p in (((r.get("detail") or {}).get("layers")) or {}).items():
            if p.get("data_b64"):
                np.save(out / f"{r['pipeline']}__{lname}.npy", decode_layer(p))
                p["data_b64"] = f"{r['pipeline']}__{lname}.npy"
        results.append(r)
    (out / "run.json").write_text(json.dumps({**{k: v for k, v in state.items() if k != "results"}, "results": results},
                                             default=str, indent=1))
    show(state, save_dir=out)
    return Path(shutil.make_archive(str(out), "zip", out))


def download(path: str | os.PathLike) -> None:
    from google.colab import files
    files.download(str(path))


def to_drive(path: str | os.PathLike, folder: str = "nisar_runs") -> Path:
    from google.colab import drive
    drive.mount("/content/drive")
    dest = Path("/content/drive/MyDrive") / folder
    dest.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(path, dest))


# ----------------------------------------------------------------------------- 5. offline self-test
def selftest(repo: str | os.PathLike | None = None) -> int:
    """The repo's own offline suites for retrieval + the scan path (local range server, fake retriever;
    no Earthdata, no network beyond pip). ~3 min. Returns pytest's exit code."""
    repo = Path(repo or REPO)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pytest", "httpx"], check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NISAR_", "EARTHDATA_"))}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "tests/test_retrieval.py", "tests/test_scans_api.py"], cwd=str(repo), env=env).returncode


# ----------------------------------------------------------------------------- 6. optional: the FastAPI on Colab
def serve(port: int = 8900, *, tunnel: bool = False, ui_origin: str | None = None) -> str:
    """Start engine.api.main:app in this process (shares the notebook's runner and verdicts.db).

    tunnel=True also opens a Cloudflare quick tunnel and returns its https URL, which you put in the UI's
    NEXT_PUBLIC_ENGINE_API. WARNING: that URL is unauthenticated and scans are enabled, so anyone holding it
    can start scans on your Earthdata token. Treat it as a secret and stop the tunnel when finished (stop_serve).
    ui_origin: extra CORS origin to allow (exact scheme+host+port); localhost:3000 is always allowed."""
    if ui_origin:
        os.environ["NISAR_UI_ORIGINS"] = ui_origin
    import uvicorn
    cfg = uvicorn.Config("engine.api.main:app", host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, name="engine-api", daemon=True).start()
    _HANDLE["server"] = server
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("uvicorn did not start")
    url = f"http://127.0.0.1:{port}"
    if tunnel:
        exe = CONTENT / "cloudflared"
        if not exe.exists():
            subprocess.run(["wget", "-q", "-O", str(exe),
                            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"], check=True)
            exe.chmod(0o755)
        proc = subprocess.Popen([str(exe), "tunnel", "--no-autoupdate", "--url", url],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        _HANDLE["tunnel"] = proc
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line or "")
            if m:
                url = m.group(0)
                break
        else:
            raise RuntimeError("cloudflared gave no URL within 60 s")
    print("engine API:", url)
    return url


def stop_serve() -> None:
    proc = _HANDLE.pop("tunnel", None)
    if proc:
        proc.terminate()
    server = _HANDLE.pop("server", None)
    if server:
        server.should_exit = True
