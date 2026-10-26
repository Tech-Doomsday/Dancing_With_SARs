#!/bin/bash
# Parallel ranged download of the NISAR granule via the presigned URL.
# Re-runnable: completed parts are skipped. Validates final size.
# Parts and the assembled granule land in <app>/data/raw/ (git-ignored).
set -u
URL="$(cat /tmp/asf/presigned.txt)"
TOTAL=2575302656
PARTS=12
OUTFILE="NISAR_L2_PR_GUNW_021_013_D_071_022_4000_SH_20260522T024038_20260522T024116_20260603T024038_20260603T024115_P05023_N_F_J_001.h5"
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$APP_ROOT/data/raw/parts"
OUT="$APP_ROOT/data/raw/$OUTFILE"
mkdir -p "$DIR"
CHUNK=$(( (TOTAL + PARTS - 1) / PARTS ))

pids=""
launched=0
for i in $(seq 0 $((PARTS-1))); do
  START=$(( i * CHUNK ))
  [ $START -ge $TOTAL ] && break
  END=$(( START + CHUNK - 1 ))
  [ $END -ge $TOTAL ] && END=$(( TOTAL - 1 ))
  EXPECT=$(( END - START + 1 ))
  PF="$DIR/part_$(printf %02d $i)"
  if [ -f "$PF" ] && [ "$(stat -c%s "$PF" 2>/dev/null || echo 0)" -eq "$EXPECT" ]; then
    echo "[skip] part $i complete"
    continue
  fi
  # resume partial parts
  RESUME_FROM=$(stat -c%s "$PF" 2>/dev/null || echo 0)
  RS=$(( START + RESUME_FROM ))
  ( if [ "$RESUME_FROM" -gt 0 ]; then
      curl -sS --retry 5 --retry-delay 3 --retry-all-errors -r $RS-$END -o - "$URL" >> "$PF"
    else
      curl -sS --retry 5 --retry-delay 3 --retry-all-errors -r $START-$END -o "$PF" "$URL"
    fi ) &
  pids="$pids $!"
  launched=$((launched+1))
done
echo "[info] launched $launched parallel part downloads"
FAIL=0
for p in $pids; do wait $p || FAIL=1; done
[ $FAIL -eq 1 ] && { echo "[error] some part failed - re-run to resume"; exit 1; }

# verify all parts
for i in $(seq 0 $((PARTS-1))); do
  START=$(( i * CHUNK )); [ $START -ge $TOTAL ] && break
  END=$(( START + CHUNK - 1 )); [ $END -ge $TOTAL ] && END=$(( TOTAL - 1 ))
  EXPECT=$(( END - START + 1 ))
  PF="$DIR/part_$(printf %02d $i)"
  GOT=$(stat -c%s "$PF" 2>/dev/null || echo 0)
  [ "$GOT" -ne "$EXPECT" ] && { echo "[error] part $i size $GOT != $EXPECT"; exit 1; }
done
echo "[ok] all parts verified, concatenating..."
cat "$DIR"/part_* > "$OUT"
GOT=$(stat -c%s "$OUT")
echo "final size: $GOT (expected $TOTAL)"
[ "$GOT" -ne "$TOTAL" ] && { echo "[error] size mismatch"; exit 1; }
echo "HDF5 magic: $(head -c 8 "$OUT" | od -An -c | head -1)"
echo "[done] $OUT"
