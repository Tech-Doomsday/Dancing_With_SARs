# Colab package

## The hub: one file, one link (start here)

`python colab/build_hub.py` writes **`dist/nisar_hub.pyz`** (~0.5 MB: engine + config + the page; no tests, docs or UI source).
Upload it to `/content` in Colab and run, in a notebook cell:

```python
import runpy; runpy.run_path("/content/nisar_hub.pyz", run_name="__main__")
```

It unpacks to `/content/nisar-hub`, installs what Colab lacks (a minute, once per runtime), takes your Earthdata token from the `.env`
it was built with (else Colab Secrets `EARTHDATA_TOKEN`, else a hidden prompt), opens a Cloudflare tunnel and prints **one link ending in `?k=<secret>`**.
Open it:

* **Retrieve**: type latitude, longitude and a size in km. The page shows the 10 km engine tiles that cover it (a 30 km box is
  9 to 16 whole tiles) and which are already on disk. *Plan: measure size* finds the granules and gives the exact bytes without
  downloading data; *Download* fetches them tile by tile. The queue runs inside Colab, so closing the tab does not stop it.
* **Datasets**: disk use, a map of every stored tile, per retrieval its granules, and every stored array as an image (click one:
  measured min / max / mean / histogram, click a pixel to read its stored value). *Download zip* keeps a retrieval.

Everything is stored under **`/content/datasets`** (local disk: SQLite needs real file locks, so not Drive). It is wiped when
the session ends: use *Download zip*, or `nc.to_drive(...)`.

The tunnel URL is public, so every request needs the secret: the link sets it as a cookie and strips it from the address bar;
anything without it gets a 401. Treat the link like a password. Stop the hub with `import nisar_colab as nc; nc.stop_serve()`.

| Build | Result |
| --- | --- |
| `python colab/build_hub.py` | packs the repo **and your root `.env`** (token included, no prompt in Colab). The file is then SECRET: never share it. `dist/` is git-ignored. No other `.env*` is ever packed |
| `... --no-env` | no credentials inside: Colab reads `EARTHDATA_TOKEN` from Secrets or asks (use this for a file you hand to others) |
| `... --wheels` | also packs the pinned dependencies as wheels (~90 MB; build machine needs network). Colab installs from them first, PyPI only fills gaps |
| `... --out PATH`, `--py 3.11` | output path; Python version the wheels are for (default 3.12) |

Options in a notebook: `os.environ["NISAR_HUB_ARGS"] = "--port 8901 --concurrency 32"` before the cell
(`--data-dir`, `--no-tunnel` also exist). From a shell cell: `!EARTHDATA_TOKEN=... python -u /content/nisar_hub.pyz`.
The hub takes at most 25 tiles per request (`NISAR_HUB_MAX_TILES`), because a 10 km tile of GCOV + GUNW is hundreds of MB.

## The full zip (notebook, scan.py, local UI)

One zip, same code as the local repo. `engine/`, `config/`, `app-ui/` are untouched by Colab; `colab/` is a thin driver.

## Build the zip (on your machine)

1. Put your token in `.env` at the repo root: `EARTHDATA_TOKEN=...`
   (https://urs.earthdata.nasa.gov/profile -> Generate Token; the profile also needs "Alaska Satellite Facility Data Access" authorized).
2. `python colab/build_colab_zip.py` -> `dist/nisar-hazard-engine-colab.zip` (repo + your `.env`; no node_modules, no data/).

The zip holds your token. Do not put it on a public drive or repo.

## Use it on Colab

1. Open `colab/NISAR_Hazard_Engine_Colab.ipynb` in Colab (File > Upload notebook), CPU runtime.
2. Upload the zip to `/content`.
3. Run cell 1 (unzip, `.env`, dependencies, credentials, tuning), then cell 2 (scan), 3 (results), 4 (download).

Without the notebook: `!unzip -q -o /content/nisar-hazard-engine-colab.zip -d /content/nisar-hazard-engine`, then
`!python -u /content/nisar-hazard-engine/colab/scan.py --lon 90.41849 --lat 23.748 --lookback 60`.

## Where settings come from (highest wins)

`overrides=` in `nc.configure/bootstrap` > NISAR_* keys in `.env` > Colab sizing (data on local disk, scans on, 64 requests in flight, caches sized to RAM/disk).
`NISAR_DATA_DIR` / `NISAR_DB_DIR` always point at the runtime's local disk (a laptop path in `.env` is ignored here).
Credentials: `.env` > environment > Colab Secrets > hidden prompt.

## Live progress

INFO logging is on. Each ~6 s: step, MB on the wire (and useful MB), MB/s now / average, requests, in flight, retries, URL resolves,
seconds since the last byte, granule N/M with a rough ETA. Per granule: one line when it finishes. `NISAR_LOG_LEVEL=WARNING` silences it.

## Local web UI + Colab network

`!python -u .../colab/serve.py` (or the notebook's last cells) prints a tunnel URL. In the local UI, click the engine pill in the nav bar (top-left, next to Landing · Map · Background), paste the URL and press Link — it is health-checked, remembered in the browser, and the page reloads onto it. (Alternative: set `NEXT_PUBLIC_ENGINE_API` in `app-ui/.env.local` and restart `npm run dev`.) Use *Use default* in the same popover to go back to the local engine. The URL is unauthenticated and scans are enabled: keep it private, stop the cell when done.

## Retrievals are stored on the runtime disk (UI-build-3)

Background → Data retrieval → "Plan & download" writes a retrieval (manifest + stored clips) under the engine's data
directory. On Colab that is the **runtime disk — lost when the session ends**. Use "Download retrieval (zip)" in the
UI, or run the optional last notebook cell to copy `retrievals/` and `clips/` to Google Drive.

Credentials: `colab/build_colab_zip.py` no longer packs your `.env` by default (use Colab Secrets → `EARTHDATA_TOKEN`,
or the hidden prompt). `--include-env` restores the old behaviour — then rotate the credentials if the zip leaves your machine.
