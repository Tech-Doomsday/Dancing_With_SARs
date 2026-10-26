#!/usr/bin/env python3
"""
Fetch an authenticated NISAR product granule (HDF5 + official browse PNGs)
from the ASF Earthdata cloud.

Auth flow (verified against the live service, 2026-10-20):
  1. GET https://nisar.asf.earthdatacloud.nasa.gov/NISAR/<collection>/<granule>/<granule>.h5
     with header `Authorization: Bearer <Earthdata token>`
       -> 303 redirect to a short-lived (~3 h) presigned CloudFront/S3 URL
          (an asf-urs session cookie is set alongside).
  2. GET the presigned URL WITHOUT the Authorization header. (S3 rejects
     requests carrying both the query-string signature and an Authorization
     header with 400 "Only one auth mechanism allowed", so the redirect must
     be followed manually rather than automatically.)
  3. OBSERVED: the bearer token succeeded for exactly one presign exchange;
     subsequent presign attempts returned 401 invalid_token while the
     presigned URL and session cookie remained valid. Treat the token as
     single-use for presigning and reuse the URL/cookie for the transfer.

For multi-GB granules a single stream throttles to ~2 MB/s; use
tools/parallel_fetch.sh (12 parallel HTTP-range parts, ~19 MB/s) once a
presigned URL is known (it reads the URL from /tmp/asf/presigned.txt or can
be edited inline).

The token is read from the EARTHDATA_BEARER_TOKEN environment variable
(lives in .env, which is git-ignored). The token is never logged or written
to any output file.

Browse PNGs (<granule>_LATLON.png / _NATIVE.png in the same granule
directory) were anonymously accessible on 2026-10-20 morning but later the
same day redirected to Earthdata Login; the script tries them anonymously
first and treats failure as non-fatal.

Usage:
  EARTHDATA_BEARER_TOKEN=... python3 tools/fetch_nisar_granule.py \
      <full-.h5-url-or-granule-id> <outdir> [--also-browse]

Resume: if a partial file exists, the download resumes via HTTP Range on a
freshly presigned URL.
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L2_GUNW_PROVISIONAL_V1"
CHUNK = 4 * 1024 * 1024
UA = "MOTUS/1.0 (NASA Space Apps 2026; science data ingest)"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirect)


def presign(url: str, token: str) -> str:
    """Exchange the Earthdata bearer token for a presigned URL."""
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": UA,
    })
    try:
        resp = NO_REDIRECT_OPENER.open(req, timeout=90)
        resp.close()
        return url  # no redirect happened (already open / anonymous)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location")
            if loc:
                return loc
            raise
        raise


def head_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("Content-Length", 0))
    except Exception:
        return 0


def download(url: str, dest: str) -> str:
    """Stream `url` to `dest` with Range-resume. Returns 'ok' | 'skip'."""
    if os.path.exists(dest):
        have = os.path.getsize(dest)
        total = head_size(url)
        if total and have == total:
            print(f"[skip] {dest} already complete ({have/1e6:.1f} MB)", flush=True)
            return "skip"
    part = os.path.getsize(dest) if os.path.exists(dest) else 0
    headers = {"User-Agent": UA}
    if part:
        headers["Range"] = f"bytes={part}-"
    req = urllib.request.Request(url, headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        if part and r.status == 206:
            total = part + int(r.headers.get("Content-Length", 0))
            mode = "ab"
        else:
            part = 0
            total = int(r.headers.get("Content-Length", 0))
            mode = "wb"
        done = part
        with open(dest, mode) as fh:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total:
                    rate = (done - part) / max(1e-6, time.time() - t0) / 1e6
                    if (done // CHUNK) % 8 == 0:
                        print(f"[..] {done/1e6:8.1f} / {total/1e6:.1f} MB  {rate:5.1f} MB/s", flush=True)
    print(f"[ok] {dest}  {done/1e6:.1f} MB in {time.time()-t0:.0f}s", flush=True)
    return "ok"


def try_browse(granule_dir_url: str, granule_id: str, outdir: str):
    """Open-access browse PNGs; best effort only."""
    for suffix in ("_LATLON.png", "_NATIVE.png"):
        url = f"{granule_dir_url}/{granule_id}{suffix}"
        dest = os.path.join(outdir, f"{granule_id}{suffix}")
        if os.path.exists(dest):
            continue
        try:
            download(url, dest)
        except Exception as e:
            print(f"[warn] browse {suffix} failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="full .h5 URL or granule ID")
    ap.add_argument("outdir")
    ap.add_argument("--also-browse", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("EARTHDATA_BEARER_TOKEN", "").strip()
    if not token:
        print("EARTHDATA_BEARER_TOKEN not set (put it in .env)", file=sys.stderr)
        sys.exit(2)

    if args.target.startswith("http"):
        h5_url = args.target.rstrip("/")
        granule_id = h5_url.rsplit("/", 1)[-1].removesuffix(".h5")
    else:
        granule_id = args.target.removesuffix(".h5")
        h5_url = f"{BASE}/{granule_id}/{granule_id}.h5"

    os.makedirs(args.outdir, exist_ok=True)

    print(f"[info] granule: {granule_id}", flush=True)
    presigned = presign(h5_url, token)
    size = head_size(presigned)
    print(f"[info] presigned OK, content-length: {size/1e6:.1f} MB" if size else "[info] presigned OK", flush=True)

    dest = os.path.join(args.outdir, f"{granule_id}.h5")
    download(presigned, dest)

    if args.also_browse:
        granule_dir_url = h5_url.rsplit("/", 1)[0]
        try_browse(granule_dir_url, granule_id, args.outdir)

    print("[done]", flush=True)


if __name__ == "__main__":
    main()
