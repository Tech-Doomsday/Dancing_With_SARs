# Colab package

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
