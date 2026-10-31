"""config/preview.yaml loader for the background preview (render version, auto scale / colour map).

Resolution order for the file: NISAR_PREVIEW_CONFIG, then <NISAR_CONFIG_DIR>/preview.yaml, then the
repo's config/ folder (never a path relative to this module alone). A missing or corrupt file raises
PreviewConfigError with a plain message; the routes answer it, the UI shows it — nothing crashes."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from engine.common.config import repo_root

SCALES = ("auto", "db", "linear")


class PreviewConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreviewCfg:
    render_version: int
    scale_by_kind: dict
    cmap_by_kind: dict
    default_cmap: str
    unit_by_scale: dict

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
        return PreviewCfg(rv, maps["scale_by_kind"], maps["cmap_by_kind"], str(raw.get("default_cmap") or "viridis"),
                          maps["unit_by_scale"])
    except (KeyError, ValueError) as err:
        raise PreviewConfigError(f"preview config {path.name} is invalid: {err}")
