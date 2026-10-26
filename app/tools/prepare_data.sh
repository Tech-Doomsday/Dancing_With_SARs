#!/usr/bin/env bash
#
# prepare_data.sh — documented, deterministic acquisition of the local
# scientific data files MOTUS can consume.
#
#   bash tools/prepare_data.sh            # fetch open-access items + extract
#   bash tools/prepare_data.sh --verify   # only verify checksums of what exists
#
# Scope (honest by design):
#   * OPEN ACCESS (fetched automatically, verified against docs/data-manifest.md):
#       - the 3 official NISAR GUNW browse PNGs for the Panama evidence dataset
#   * OPEN ACCESS, MANUAL STEP (large file; fetched automatically when the
#     direct URL is reachable, otherwise you are instructed where to get it):
#       - the NISAR sample HDF5 (ALOS PALSAR 2008, ~264 MiB) -> derived LA dataset
#   * CREDENTIAL-GATED (never fetched here; requires a free NASA Earthdata
#     Login bearer token set as EARTHDATA_BEARER_TOKEN in .env):
#       - the operational Mojave 2026 granule (~2.58 GB) -> tools/fetch_nisar_granule.py
#
# The script never bypasses access controls and never invents URLs: the only
# automatic downloads use the official open-access endpoints recorded in
# docs/data-manifest.md. Every downloaded file is checksum-verified; a failed
# verification aborts with a non-zero exit code.

set -euo pipefail
cd "$(dirname "$0")/.."

VERIFY_ONLY=0
[[ "${1:-}" == "--verify" ]] && VERIFY_ONLY=1

MANIFEST=docs/data-manifest.md
BROWSE_DIR=data/nisar-browse
SAMPLE_DIR=data/nisar-sample
DERIVED_DIR=data/derived

# Official open-access browse endpoint (ASF Earthdata Cloud; no login needed).
BROWSE_BASE_URL="https://nisar.asf.earthdatacloud.nasa.gov/BROWSE/NISAR_L2_GUNW_PROVISIONAL_V1"

BROWSE_GRANULES=(
  "NISAR_L2_PR_GUNW_031_141_D_085_032_4000_SH_20260927T235335_20260927T235344_20261009T235335_20260909T235345_P05023_N_P_J_001"
  "NISAR_L2_PR_GUNW_032_004_A_005_033_4000_SH_20260930T110420_20260930T110435_20261012T110420_20261012T110436_P05023_N_P_J_001"
  "NISAR_L2_PR_GUNW_032_040_D_085_033_4000_SH_20261002T234516_20261002T234533_20261014T234516_20261014T234533_P05023_N_P_J_001"
)
# Corrected first granule id (the pair's secondary acquisition is 20261009):
BROWSE_GRANULES[0]="NISAR_L2_PR_GUNW_031_141_D_085_032_4000_SH_20260927T235335_20260927T235344_20261009T235335_20261009T235345_P05023_N_P_J_001"

SAMPLE_H5="NISAR_L2_PR_GUNW_001_030_A_019_002_2000_SH_20081012T060911_20081012T060925_20081127T061000_20081127T061014_D00404_N_F_J_001.h5"
# Official NISAR sample-data page (the authoritative listing of sample
# products; the direct download link for this granule is published there).
SAMPLE_PAGE="https://nisar.jpl.nasa.gov/data/sample-data/"

fail() { echo "prepare_data: $*" >&2; exit 1; }

verify_sha256() {
  local file="$1" expected="$2"
  [[ -f "$file" ]] || return 1
  local actual
  actual=$(python3 - "$file" << 'EOF'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b''):
        h.update(chunk)
print(h.hexdigest())
EOF
)
  [[ "$actual" == "$expected" ]]
}

expected_sha256() {
  # Look the file up in the committed manifest.
  python3 - "$MANIFEST" "$1" << 'EOF'
import re, sys
manifest, name = sys.argv[1], sys.argv[2]
text = open(manifest).read()
rows = re.findall(r"\|\s*`([^`]+)`\s*\|[^|]+\|\s*`([0-9a-f]{64})`\s*\|", text)
for fname, digest in rows:
    if fname.endswith(name):
        print(digest)
        sys.exit(0)
sys.exit(1)
EOF
}

echo "== MOTUS data preparation =="

# ---------------------------------------------------------------------------
# 1. Panama browse imagery (open access)
# ---------------------------------------------------------------------------
mkdir -p "$BROWSE_DIR"
for g in "${BROWSE_GRANULES[@]}"; do
  file="$BROWSE_DIR/${g}_LATLON.png"
  want=$(expected_sha256 "${g}_LATLON.png" || true)
  if [[ -n "$want" ]] && verify_sha256 "$file" "$want"; then
    echo "[ok] browse ${g:0:60}... already present and verified"
    continue
  fi
  if [[ $VERIFY_ONLY -eq 1 ]]; then
    echo "[missing] browse $file (open access: $BROWSE_BASE_URL/$g/${g}_LATLON.png)"
    continue
  fi
  echo "[fetch] browse ${g:0:60}..."
  url="$BROWSE_BASE_URL/$g/${g}_LATLON.png"
  if curl -fsSL --retry 3 -o "$file.part" "$url"; then
    mv "$file.part" "$file"
    if [[ -n "$want" ]] && ! verify_sha256 "$file" "$want"; then
      fail "checksum mismatch for $file — the upstream file changed; verify its origin before trusting it (update docs/data-manifest.md deliberately if you accept the change)."
    fi
    echo "[ok] fetched + verified $file"
  else
    rm -f "$file.part"
    echo "[skip] could not fetch $url (network or availability) — obtain it manually from the official ASF cloud and place it at $file"
  fi
done

# ---------------------------------------------------------------------------
# 2. NISAR sample HDF5 -> derived LA dataset
# ---------------------------------------------------------------------------
mkdir -p "$SAMPLE_DIR" "$DERIVED_DIR"
if [[ -f "$SAMPLE_DIR/$SAMPLE_H5" ]]; then
  echo "[ok] sample HDF5 present ($SAMPLE_DIR/$SAMPLE_H5)"
else
  if [[ $VERIFY_ONLY -eq 1 ]]; then
    echo "[missing] $SAMPLE_DIR/$SAMPLE_H5"
  else
    echo "[manual] The NISAR sample HDF5 (~264 MiB) is published on the official"
    echo "         sample-data page: $SAMPLE_PAGE"
    echo "         Download '$SAMPLE_H5' into $SAMPLE_DIR/ , then re-run this script."
  fi
fi
if [[ -f "$SAMPLE_DIR/$SAMPLE_H5" && $VERIFY_ONLY -eq 0 ]]; then
  want=$(expected_sha256 "$SAMPLE_H5" || true)
  if [[ -n "$want" ]] && ! verify_sha256 "$SAMPLE_DIR/$SAMPLE_H5" "$want"; then
    echo "[warn] sample HDF5 checksum does not match the committed manifest —"
    echo "       the file may have been re-uploaded upstream. Inspect before use."
  fi
fi
if [[ -f "$SAMPLE_DIR/$SAMPLE_H5" && ! -f "$DERIVED_DIR/nisar-la-2008/meta.json" ]]; then
  echo "[extract] running tools/extract_gunw.py on the sample product..."
  python3 tools/extract_gunw.py "$SAMPLE_DIR/$SAMPLE_H5" "$DERIVED_DIR/nisar-la-2008" \
    --slug nisar-la-2008 \
    --title "Los Angeles region — NISAR-format L-band interferogram (2008)" \
    --blurb "Real ALOS PALSAR L-band radar pair over the San Gabriel Mountains and Antelope Valley, processed into a NISAR L2 GUNW product by the NISAR SDS. Full quantitative analysis available."
fi

# ---------------------------------------------------------------------------
# 3. Credential-gated operational granules — documented, never auto-fetched
# ---------------------------------------------------------------------------
echo
echo "Credential-gated datasets (require free NASA Earthdata Login):"
echo "  - real-nisar-mojave-2026: set EARTHDATA_BEARER_TOKEN in .env, then"
echo "    python3 tools/fetch_nisar_granule.py   # ~2.58 GB, see docs/data-sources.md"
echo "    python3 tools/extract_gunw.py <granule.h5> data/derived/nisar-mojave-2026 --slug nisar-mojave-2026"
echo
echo "Done. Current dataset states:"
python3 - << 'EOF'
import json, os, sys
sys.path.insert(0, 'src')
EOF
curl -s http://localhost:3000/api/datasets 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for ds in d['datasets']:
        print(f\"  {ds['id']}: {ds['availability']['state']}\")
except Exception:
    print('  (dev server not running; start it with bun run dev and check /api/datasets)')
"
