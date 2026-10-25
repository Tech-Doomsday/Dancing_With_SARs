"""P08 - backscatter prep: gamma0 in dB and HV/HH ratio on G20.

With a GUNW-only workflow there is no GCOV clip: the stage is then an explicit,
logged skip (flag BACKSCATTER_UNAVAILABLE) rather than a silent absence.
Averaging to G20 happened in LINEAR power (P04); the dB conversion is last.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from ._types import Grid
from .config import PreprocConfig
from .p02_clip import GCOV_POLS
from .state import F_NO_BACKSCATTER, BundleBuilder

STAGE = "P08"


def to_db(lin: np.ndarray, cfg: PreprocConfig) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        db = np.float32(10.0) * np.log10(lin.astype(np.float32, copy=False))
    db[~np.isfinite(db)] = np.nan
    with np.errstate(invalid="ignore"):
        np.clip(db, cfg.gamma0_floor_db, cfg.gamma0_ceiling_db, out=db)
    return db


def run(state: BundleBuilder) -> Dict:
    pols = [p for p in GCOV_POLS if p in state.work20]
    if not pols:
        state.flag(F_NO_BACKSCATTER)
        state.meta["backscatter_available"] = False
        return {"skipped": True, "reason": "no GCOV clip for current epoch"}
    for p in pols:
        state.put(f"{p}_db", to_db(state.work20[p], state.cfg), "G20")
    ratio_name = None
    if "gamma0_hv" in pols:
        for co in ("hh", "vv"):
            if f"gamma0_{co}" in pols:
                ratio_name = f"hv_{co}_ratio_db"
                state.put(ratio_name, (state.layers["gamma0_hv_db"] - state.layers[f"gamma0_{co}_db"]).astype(np.float32), "G20")
                break
    state.meta["backscatter_available"] = True
    state.meta["backscatter_pols"] = pols
    return {"pols": pols, "ratio": ratio_name}
