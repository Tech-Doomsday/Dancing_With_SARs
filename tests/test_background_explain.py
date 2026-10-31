"""Corner geometry for stored clips / trace arrays and inventory annotation (copy tests live in test_explain.py;
route tests live in test_background_api.py)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from engine.common.grids import edge_corners_lonlat, extent_corners_lonlat, make_grid

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ corners
class _Grp:
    def __init__(self, x, y, epsg):
        self._a = {"x": x, "y": y} if x is not None else {}
        self.attrs = {"epsg": epsg}
    def array_keys(self): return list(self._a)
    def __getitem__(self, k): return self._a[k]


def test_stored_clip_corners_from_axes_or_null_never_a_guess():
    from engine.api.retrievals import clip_corners
    x = 650_000.0 + 20.0 * np.arange(50) + 10.0
    y = 3_770_000.0 - 20.0 * np.arange(40) - 10.0
    c, note = clip_corners(_Grp(x, y, 32611))
    assert note is None and len(c) == 4
    assert c[0][0] < c[1][0] and c[0][1] > c[3][1]            # TL is west/north of TR / BL
    none, why = clip_corners(_Grp(None, None, 32611))
    assert none is None and "no x / y axes" in why


def test_trace_array_corners_match_the_tile_grid():
    g = make_grid("G80", 32611, 650_000.0, 3_770_000.0, 4000.0, 80.0, 400.0)       # epsg, e0, n0 as in grids.py
    from_axes, _ = edge_corners_lonlat(g.x, g.y, g.epsg)
    extent = (float(g.x[0] - 40), float(g.x[-1] + 40), float(g.y[-1] - 40), float(g.y[0] + 40))   # pixel edges
    from_extent, note = extent_corners_lonlat(extent, g.epsg)
    assert note is None and np.allclose(from_axes, from_extent)
    assert extent_corners_lonlat(None, 32611)[0] is None
    assert extent_corners_lonlat(extent, None)[0] is None


# ------------------------------------------------------------------ inventory annotation
def test_inventory_layers_get_role_kind_previewable_and_cmap_from_the_engine():
    from engine.api import background as bg
    gcov = "science__LSAR__GCOV__grids__frequencyA__HHHH"
    gunw = "science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram__HH__unwrappedPhase"
    inv = {"grids": [{"layers": [
        {"key": gcov, "dtype": "<f4", "readable": True},
        {"key": gunw, "dtype": "<f4", "readable": True},
        {"key": "no__known__role", "dtype": "<f4", "readable": True},
        {"key": gcov, "dtype": "[('r', '<f4')]", "readable": True},
        {"key": gcov, "dtype": "<f4", "readable": False},
    ]}]}
    bg._annotate_inventory(inv)
    a, b, c, d, e = inv["grids"][0]["layers"]
    assert (a["role"], a["kind"], a["previewable"], a["default_cmap"]) == ("cov", "power", True, "gray")
    assert (b["role"], b["previewable"], b["default_cmap"]) == ("unw_phase", True, "diverging")
    assert c["previewable"] is False and c["what_it_is"] is None and c["role"] is None     # no role -> no sentence
    assert d["previewable"] is False and e["previewable"] is False                        # structured / unreadable
