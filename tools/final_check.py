#!/usr/bin/env python3
"""ONE-SHOT acceptance check for the retrieval layer. Run it once; paste the whole output back.

    python tools/final_check.py

Needs .env with EARTHDATA_TOKEN (or EARTHDATA_USERNAME/PASSWORD). Takes ~5-10 minutes on a slow link.
It runs the unit tests, polls ASF, then on REAL granules (one GUNW, one GCOV) fetches an MGRS tile,
compares every value with plain h5py, tests the cache tiers, a restart, a neighbouring tile and four
concurrent processes sharing one cache; finally it copies the learned catalogs into config/catalog/.
Every step is isolated: one failure never stops the rest. Report: <data dir>/final_check_report.txt
"""
from __future__ import annotations

import argparse
import importlib
import multiprocessing as mp
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAYERS = {
    "GUNW": r"unwrappedInterferogram/HH/(unwrappedPhase|coherenceMagnitude|connectedComponents)",
    "GCOV": r"grids/frequencyA/(HHHH|HVHV|mask|numberOfLooks)",
}
LA_REGION = (-119.0, 33.8, -117.8, 34.8)          # known to contain NISAR GUNW + GCOV (earlier probes)
BD_REGION = (89.8, 22.4, 91.2, 24.6)              # Bangladesh / Dhaka, informational only
RESULTS: list[tuple[str, str, str]] = []
LOG: list[str] = []


def out(msg=""):
    print(msg, flush=True)
    LOG.append(str(msg))


def record(name, status, detail=""):
    RESULTS.append((name, status, detail))
    out(f"[{status:4}] {name}" + (f" - {detail}" if detail else ""))


def step(name):
    """Run a check; any exception becomes a FAIL with the traceback tail, and the script goes on."""
    def deco(fn):
        def run(*a, **k):
            out(f"\n=== {name}")
            t = time.time()
            try:
                return fn(*a, **k)
            except Exception as ex:
                tb = "".join(traceback.format_exception_only(type(ex), ex)).strip()
                record(name, "FAIL", f"{tb[:300]}")
                out("      " + "\n      ".join(traceback.format_exc().strip().splitlines()[-8:]))
            finally:
                out(f"    ({time.time() - t:.1f}s)")
        return run
    return deco


# --------------------------------------------------------------------------- steps
@step("install integrity")
def check_install():
    """Every shipped code file must match the manifest, otherwise results would describe old code."""
    import hashlib, json
    mf = ROOT / "MANIFEST.json"
    if not mf.exists():
        record("install integrity", "WARN", "no MANIFEST.json (cannot verify the install)")
        return True
    want = json.loads(mf.read_text())
    strict, soft = [], []
    for rel, h in want.items():
        p = ROOT / rel
        got = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if p.exists() else None
        if got != h:
            (soft if rel.startswith("config/") or rel.endswith((".md", ".example", ".gitignore")) else strict).append(
                rel + ("" if p.exists() else "  (MISSING)"))
    if strict:
        out("  files that are old or missing:\n    " + "\n    ".join(strict[:25]))
        record("install integrity", "FAIL", f"{len(strict)} code file(s) are stale/missing -> re-run the install block (it copies over ALL files)")
        return False
    record("install integrity", "PASS" if not soft else "WARN",
           f"all {len(want) - len(soft)} code files match" + (f"; {len(soft)} config/doc file(s) differ (fine if you edited them): {soft[:4]}" if soft else ""))
    return True


@step("environment")
def check_env(cfg, auth):
    out(f"python {platform.python_version()} on {platform.platform()}")
    bad = []
    for mod, dist in (("numpy", "numpy"), ("h5py", "h5py"), ("aiohttp", "aiohttp"), ("zarr", "zarr"), ("pyproj", "pyproj"),
                      ("shapely", "shapely"), ("yaml", "pyyaml"), ("asf_search", "asf_search")):
        try:
            importlib.import_module(mod)
            from importlib.metadata import version
            out(f"  {dist:12} {version(dist)}")
        except Exception:
            bad.append(dist)
    if bad:
        record("environment", "FAIL", "missing: " + ", ".join(bad) + "  ->  pip install -r requirements-retrieval.txt")
        return
    out(f"  data dir    {cfg.data_dir}")
    free = shutil.disk_usage(cfg.data_dir if cfg.data_dir.exists() else ROOT).free / 1e9
    record("environment", "PASS" if free > 5 else "WARN", f"deps ok, {free:.0f} GB free in data dir")
    record("credentials", "PASS" if auth.available else "FAIL",
           "Earthdata credentials found" if auth.available else "set EARTHDATA_TOKEN in .env")


@step("unit tests (local mock server)")
def run_pytest():
    def run():
        return subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-rfE", "--tb=short"],
                              cwd=ROOT, capture_output=True, text=True, timeout=900)
    r = run()
    if "No module named pytest" in r.stderr + r.stdout:
        out("  installing pytest + mgrs (test-only packages) ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pytest", "mgrs"], capture_output=True, timeout=600)
        r = run()
    lines = (r.stdout + "\n" + r.stderr).splitlines()
    summary = next((l for l in reversed(lines) if " passed" in l or " failed" in l or "error" in l.lower()), "no output")
    failed = [l for l in lines if l.startswith(("FAILED ", "ERROR "))]
    if r.returncode != 0:
        i = next((k for k, l in enumerate(lines) if "FAILURES" in l or "ERRORS" in l), 0)
        j = next((k for k, l in enumerate(lines) if "short test summary" in l), len(lines))
        out("  ---- pytest failure details ----\n  " + "\n  ".join(lines[i:j][:90]))
        for l in failed:
            name, _, why = l.partition(" - ")
            record("unit test " + name.split(" ", 1)[-1].split("::")[-1], "FAIL", (why or name)[:240])
    record("unit tests", "PASS" if r.returncode == 0 else "FAIL", summary.strip("= "))


def do_poll(client, region, products, label, searcher=None):
    from engine.retrieval.scheduler.granule_poller import GranulePoller
    from engine.retrieval.state import State
    from engine.tiling.mgrs import tile_from_id
    from shapely.geometry import box

    dbp = client.cfg.data_dir / "state" / f"final_check_{label}.sqlite"
    for suf in ("", "-wal", "-shm"):
        try: Path(str(dbp) + suf).unlink()
        except OSError: pass
    client.cfg.poll.update(region=list(region), products=list(products), lookback_days=60)
    poller = GranulePoller(client, State(dbp), searcher=searcher)
    t = time.time(); r1 = poller.poll(); dt = time.time() - t
    r2 = poller.poll()
    by = {p: [x for x in r1.new if x.product == p] for p in products}
    out(f"  poll {label}: {len(r1.new)} granules in {dt:.1f}s  " + "  ".join(f"{p}={len(v)}" for p, v in by.items()))
    out(f"  queued tiles: {len(r1.dirty)}   queue: {poller.state.counts()}   sample: {sorted(r1.dirty)[:4]}")
    if r1.errors:
        record(f"poller {label}", "FAIL", f"ASF errors: {r1.errors}")
        return r1, by
    problems = []
    if r1.degraded: problems.append("served stale listing")
    if r1.no_footprint: problems.append(f"{len(r1.no_footprint)} granule(s) without footprint")
    rb = box(*region).buffer(0.2)
    stray = [t for t in r1.dirty if not box(*tile_from_id(t).bbox()).intersects(rb)]
    if stray: problems.append(f"{len(stray)} queued tile(s) outside the region")
    if r2.new: problems.append(f"2nd poll found {len(r2.new)} 'new' granules (should be 0)")
    if not r1.new: problems.append("no granules in window")
    status = "FAIL" if (stray or r2.new) else ("WARN" if problems else "PASS")
    record(f"poller {label}", status, f"{len(r1.new)} granules, {len(r1.dirty)} tiles queued" + (f"; {'; '.join(problems)}" if problems else "; 2nd poll idempotent"))
    return r1, by


def pick_tile(ref, cell):
    from shapely.geometry import Polygon
    from engine.tiling.mgrs import tile_at
    c = Polygon(ref.footprint).buffer(0).centroid
    return tile_at(c.x, c.y, cell)


def check_product(client, product, ref, cell, report_mb=True):
    """Runs the checks with brand-new clip/chunk/RAM caches (index + catalog are kept), so a re-run
    always exercises the real network path instead of being served from the previous run."""
    from engine.retrieval.clients.cache import ByteLRU, ClipStore, DiskChunkCache
    tmpd = Path(tempfile.mkdtemp(prefix="nisar_fc_"))
    saved = (client.disk, client.clips, client.ram)
    client.disk, client.clips, client.ram = DiskChunkCache(tmpd / "chunks", 1 << 33), ClipStore(tmpd / "clips", None), ByteLRU(256 << 20)
    try:
        return _check_product(client, product, ref, cell)
    finally:
        client.disk, client.clips, client.ram = saved
        shutil.rmtree(tmpd, ignore_errors=True)


def _check_product(client, product, ref, cell):
    from engine.retrieval import checks
    from engine.tiling.mgrs import Tile, tile_from_id
    from engine.retrieval.clients.nisar_client import NisarClient

    layers = LAYERS[product]
    tile = pick_tile(ref, cell)
    out(f"  {product} granule {ref.granule_id[:70]}...\n  tile {tile.id} (centre of the frame)  layers: {layers}")
    m = client.io.metrics

    def delta(before):
        now = m.snapshot()
        return {k: now[k] - before[k] for k in now}

    b = m.snapshot(); t = time.time()
    res = client.fetch_tile(tile, product=product, layers=layers, granules=[ref]); dt = time.time() - t
    d = delta(b)
    ix = client._indexes.get(ref.granule_id)
    out(f"  index: built={client.counters['index_built']} disk={client.counters['index_disk']} targeted={client.counters.get('index_targeted', 0)} "
        f"skeleton={client.counters['index_skeleton_s']:.1f}s tables={client.counters['index_tables_s']:.1f}s")
    ok = bool(res.clips) and not res.errors
    src = res.clips[0].source if res.clips else None
    record(f"{product} tile fetch", ("PASS" if src == "network" else "WARN") if ok else "FAIL",
           f"{dt:.1f}s incl. index, {d['requests']} requests, {d['bytes_fetched'] / 1e6:.1f} MB, source={src}, errors={res.errors or 'none'}")
    if not ok:
        return None
    gc = next(iter(res.clips[0].grids.values()))
    out(f"  window {len(gc.y)}x{len(gc.x)} px, layers: {sorted(k.split('__')[-1] for k in gc.layers)}")

    ver = checks.verify_clip(client, ref, res)
    bad = [k.split("__")[-1] for k, same, *_ in ver if not same]
    record(f"{product} values identical to h5py", "PASS" if not bad and ver else "FAIL",
           f"{len(ver)} arrays compared" + (f"; MISMATCH in {bad}" if bad else ""))
    vf = {k.split("__")[-1]: checks.valid_fraction(a) for g in res.clips[0].grids.values() for k, a in g.layers.items()}
    vf = {k: v for k, v in vf.items() if v is not None}
    low = [k for k, v in vf.items() if v < 0.05]
    out("  valid data: " + ", ".join(f"{k} {100 * v:.0f}%" for k, v in vf.items()))
    record(f"{product} tile has real data", "PASS" if vf and not low else "WARN",
           "all float layers >5% valid" if not low else f"nearly empty: {low} (tile may be at a no-data edge)")
    first_hash = checks.hash_clip(res)

    b = m.snapshot(); res2 = client.fetch_tile(tile, product=product, layers=layers, granules=[ref]); d = delta(b)
    record(f"{product} repeat = clip cache", "PASS" if res2.clips and res2.clips[0].source == "clip-cache" and d["requests"] == 0 else "FAIL",
           f"source={res2.clips[0].source if res2.clips else None}, {d['requests']} requests")

    c2 = NisarClient(client.cfg, client.auth)                       # simulated process restart: RAM cold, disk warm
    c2.disk, c2.clips = client.disk, client.clips
    try:
        b2 = c2.io.metrics.snapshot(); t = time.time()
        res3 = c2.fetch_tile(tile, product=product, layers=layers, granules=[ref]); dt = time.time() - t
        same = bool(res3.clips) and checks.hash_clip(res3) == first_hash
        reqs = c2.io.metrics.snapshot()["requests"] - b2["requests"]
        record(f"{product} restart reuses disk caches", "PASS" if same and reqs == 0 else "FAIL",
               f"{dt:.2f}s, {reqs} requests, identical={same}")
    finally:
        c2.close()

    nb = Tile(tile.zone, tile.band, tile.e0 + tile.size, tile.n0, tile.size)
    b = m.snapshot(); t = time.time()
    resn = client.fetch_tile(nb, product=product, layers=layers, granules=[ref]); dt = time.time() - t
    d = delta(b)
    if resn.clips:
        vern = checks.verify_clip(client, ref, resn)
        badn = [k for k, same, *_ in vern if not same]
        record(f"{product} neighbour tile {nb.id}", "PASS" if not badn else "FAIL",
               f"{dt:.1f}s, {d['requests']} requests, {d['bytes_fetched'] / 1e6:.1f} MB (chunks shared with the first tile are reused), identical to h5py={not badn}")
    else:
        record(f"{product} neighbour tile {nb.id}", "WARN", f"no data returned: {resn.errors or resn.skipped}")
    return {"tile": tile.id, "ref": ref, "layers": layers, "hash": first_hash, "product": product}


@step("4 concurrent processes on one shared cache")
def check_multiprocess(cfg, info, selftest):
    from engine.retrieval import checks
    shared = Path(tempfile.mkdtemp(prefix="nisar_mp_"))
    for sub in ("layout", "catalog"):
        src = cfg.data_dir / "cache" / sub
        if src.exists():
            shutil.copytree(src, shared / "cache" / sub)
    ref = info["ref"]
    args = {"data_dir": str(shared), "ref": ref.to_dict(), "tile": info["tile"], "layers": info["layers"], "selftest": selftest}
    old = os.environ.get("NISAR_DATA_DIR")
    t = time.time()
    try:
        with mp.get_context("spawn").Pool(4) as pool:
            outs = pool.map(checks.fetch_worker, [args] * 4)
    finally:
        if old is None: os.environ.pop("NISAR_DATA_DIR", None)
        else: os.environ["NISAR_DATA_DIR"] = old
    errs = [o["errors"] for o in outs if o["errors"] or not o["clips"]]
    hashes = {o["hash"] for o in outs}
    leftovers = list(shared.rglob("*.tmp*"))
    ok = not errs and hashes == {info["hash"]} and not leftovers
    record("concurrent processes", "PASS" if ok else "FAIL",
           f"{time.time() - t:.1f}s, identical to single-process result={hashes == {info['hash']}}, errors={errs or 'none'}, temp files left={len(leftovers)}")
    shutil.rmtree(shared, ignore_errors=True)


@step("export learned catalogs to config/catalog/")
def export_catalogs(cfg):
    dst = ROOT / "config" / "catalog"
    dst.mkdir(parents=True, exist_ok=True)
    done = []
    for f in cfg.catalog_dirs[1].glob("*.json"):
        shutil.copy2(f, dst / f.name)
        done.append(f.name)
    record("catalogs", "PASS" if done else "WARN", f"copied {done} -> config/catalog/ (commit them)" if done else "none learned")


# --------------------------------------------------------------------------- main
def main():
    try:
        _main()
    except SystemExit:
        raise
    except BaseException:
        out("\nUNEXPECTED CRASH of the check script itself:\n" + traceback.format_exc())
        record("check script", "FAIL", "crashed (see traceback above)")
        _finish(None)


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="developer mode: run against a local mock ASF")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()

    server = None
    if a.selftest and not os.environ.get("NISAR_SELFTEST_KEEP"):
        os.environ["NISAR_DATA_DIR"] = tempfile.mkdtemp(prefix="nisar_selftest_")
    from engine.retrieval.auth.secrets import EarthdataAuth, earthdata_auth
    from engine.retrieval.clients.nisar_client import NisarClient
    from engine.retrieval.clients.types import GranuleRef
    from engine.retrieval.settings import load_settings

    cfg = load_settings()
    auth = earthdata_auth()
    searcher = None
    regions = {"la": LA_REGION}
    if a.selftest:
        from synth import RangeServer, lonlat_box, make_granule
        tmp = Path(os.environ["NISAR_DATA_DIR"]) / "mock"
        tmp.mkdir(parents=True, exist_ok=True)
        name = "NISAR_L2_PR_GUNW_A_001_000_000_000_000_000_000_20260701T000000_20260701T000030_X05010_N_F_J_001.h5"
        if not (tmp / name).exists():
            make_granule(tmp / name, 1500, 1500)
        server = RangeServer(tmp, latency_s=0.02)
        w, s, e, n = lonlat_box(0, 0, 1500, 1500)
        mock = GranuleRef(name[:-3], server.url(name), "GUNW", "20261008T000000Z", "20261008T000030Z",
                          footprint=[[w, s], [e, s], [e, n], [w, n], [w, s]])
        searcher = lambda bbox, t0, t1, product: ([mock] if product == "GUNW" else [], False)
        cfg.net.update(auth_hosts=["127.0.0.1"], retries=3, connect_timeout_s=5, stall_timeout_s=10)
        auth = EarthdataAuth(token="test")
        regions = {"la": (w - .1, s - .1, e + .1, n + .1)}

    out("NISAR retrieval layer - final acceptance check" + ("  [SELFTEST against a mock]" if a.selftest else ""))
    out(f"started {time.strftime('%Y-%m-%d %H:%M:%S')}   expected duration: a few minutes (longer on slow links)")
    proceed = check_install()
    check_env(cfg, auth)
    if proceed is False:
        out("\nSTOPPED: the installed files are not the files from the zip. Run the install block again.")
    elif not auth.available:
        out("\nNo credentials: cannot continue. Create .env from .env.example, then run again.")
    else:
        if not a.selftest and not a.skip_tests:
            run_pytest()
        client = NisarClient(cfg, auth)
        try:
            r1, by = None, {}
            res = step("poller (LA region)")(do_poll)(client, regions["la"], ["GUNW", "GCOV"], "la", searcher)
            if res:
                r1, by = res
            if not a.selftest:
                step("poller (Bangladesh region, informational)")(do_poll)(client, BD_REGION, ["GUNW", "GCOV"], "bd")
            cell = int(getattr(cfg, "tiling", {}).get("cell_size_m", 10000))
            infos = {}
            for product in ("GUNW", "GCOV"):
                cands = [x for x in by.get(product, []) if x.footprint]
                if not cands:
                    record(f"{product} tile fetch", "SKIP", "no granule with footprint found in the LA region/lookback window")
                    continue
                ref = sorted(cands, key=lambda r: r.start or "")[-1]
                info = step(f"{product} checks on a real granule")(check_product)(client, product, ref, cell, True)
                if info:
                    infos[product] = info
            if infos:
                check_multiprocess(cfg, infos.get("GCOV") or infos["GUNW"], a.selftest)
            if a.selftest:
                record("catalogs", "SKIP", "not exported in selftest mode")
            else:
                export_catalogs(cfg)
        finally:
            client.close()

    _finish(cfg, server)


def _finish(cfg, server=None):
    out("\n" + "=" * 72)
    out("SUMMARY")
    for name, status, detail in RESULTS:
        out(f"  [{status:4}] {name}")
    nf = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    nw = sum(1 for _, s, _ in RESULTS if s == "WARN")
    ns = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    out(f"\nRESULT: {'ALL CHECKS PASSED' if nf == 0 else f'{nf} CHECK(S) FAILED'}   (warnings: {nw}, skipped: {ns})")
    rp = (cfg.data_dir if cfg else ROOT / "data") / "final_check_report.txt"
    try:
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("\n".join(LOG), encoding="utf-8")
        out(f"report saved: {rp}")
    except OSError:
        pass
    if server:
        server.loop.call_soon_threadsafe(server.loop.stop)
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    mp.freeze_support()
    main()
