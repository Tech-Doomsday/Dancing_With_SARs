"""config/preview.yaml loader for the background preview (render version, auto scale / colour map).

Resolution order for the file: NISAR_PREVIEW_CONFIG, then <NISAR_CONFIG_DIR>/preview.yaml, then the
repo's config/ folder (never a path relative to this module alone). A missing or corrupt file raises
PreviewConfigError with a plain message; the routes answer it, the UI shows it — nothing crashes."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engine.common.config import repo_root

SCALES = ("auto", "db", "linear")


# coherence validation: opacity ramps from min_alpha (coherence <= lo) to 1 (coherence >= hi). Used when
# preview.yaml has no `quality:` section (a custom NISAR_PREVIEW_CONFIG written before this option existed).
QUALITY_DEFAULTS = {"lo": 0.2, "hi": 0.6, "min_alpha": 0.05}


class PreviewConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreviewCfg:
    render_version: int
    scale_by_kind: dict
    cmap_by_kind: dict
    default_cmap: str
    unit_by_scale: dict
    quality: dict = field(default_factory=lambda: {"coherence": dict(QUALITY_DEFAULTS)})

    def resolve_scale(self, requested: str, kind: str | None) -> str:
        if requested != "auto":
            return requested
        return self.scale_by_kind.get(kind or "", "linear")

    def resolve_cmap(self, requested: str, kind: str | None) -> str:
        if requested != "auto":
            return requested
        return self.cmap_by_kind.get(kind or "", self.default_cmap)

    def unit(self, scale: str) -> str | None:
        return self.unit_by_scale.get(scale)          # None = unknown -> the UI shows a dash


def preview_config_path(env: dict | None = None, root: Path | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("NISAR_PREVIEW_CONFIG"):
        return Path(env["NISAR_PREVIEW_CONFIG"])
    if env.get("NISAR_CONFIG_DIR"):
        return Path(env["NISAR_CONFIG_DIR"]) / "preview.yaml"
    return (root or repo_root()) / "config" / "preview.yaml"


def load_preview_config(env: dict | None = None, root: Path | None = None) -> PreviewCfg:
    path = preview_config_path(env, root)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise PreviewConfigError(f"preview config not found: {path.name} (set NISAR_PREVIEW_CONFIG or NISAR_CONFIG_DIR)")
    except Exception as err:
        raise PreviewConfigError(f"preview config {path.name} could not be read: {type(err).__name__}: {err}")
    if not isinstance(raw, dict):
        raise PreviewConfigError(f"preview config {path.name} must be a mapping")
    try:
        rv = raw["render_version"]
        if isinstance(rv, bool) or not isinstance(rv, int):
            raise ValueError("render_version must be an integer")
        maps = {k: raw.get(k) or {} for k in ("scale_by_kind", "cmap_by_kind", "unit_by_scale")}
        for k, v in maps.items():
            if not isinstance(v, dict):
                raise ValueError(f"{k} must be a mapping")
        bad = [v for v in maps["scale_by_kind"].values() if v not in ("db", "linear")]
        if bad:
            raise ValueError(f"scale_by_kind values must be db|linear, got {bad}")
        q = {**QUALITY_DEFAULTS, **((raw.get("quality") or {}).get("coherence") or {})}
        for k in ("lo", "hi", "min_alpha"):
            if isinstance(q[k], bool) or not isinstance(q[k], (int, float)):
                raise ValueError(f"quality.coherence.{k} must be a number")
        q = {k: float(q[k]) for k in ("lo", "hi", "min_alpha")}
        if not (0.0 <= q["lo"] < q["hi"] <= 1.0):
            raise ValueError("quality.coherence needs 0 <= lo < hi <= 1")
        if not (0.0 <= q["min_alpha"] <= 1.0):
            raise ValueError("quality.coherence.min_alpha must be within 0..1")
        return PreviewCfg(rv, maps["scale_by_kind"], maps["cmap_by_kind"], str(raw.get("default_cmap") or "viridis"),
                          maps["unit_by_scale"], {"coherence": q})
    except (KeyError, ValueError) as err:
        raise PreviewConfigError(f"preview config {path.name} is invalid: {err}")
