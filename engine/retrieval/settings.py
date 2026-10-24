"""Settings: config/retrieval.yaml + .env + NISAR_* env overrides. No hardcoded paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def project_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for p in [here, *here.parents]:
        if (p / "pyproject.toml").exists() or (p / "config").is_dir():
            return p
    return Path.cwd()


def _coerce(raw: str, like: Any) -> Any:
    if like is None:
        return None if raw.lower() in ("", "null", "none") else float(raw) if raw.replace(".", "", 1).isdigit() else raw
    if isinstance(like, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(raw)
    if isinstance(like, float):
        return float(raw)
    if isinstance(like, list):
        return [s.strip() for s in raw.split(",") if s.strip()]
    return raw


def _apply_env(section: str, values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if isinstance(v, dict):
            out[k] = _apply_env(f"{section}_{k}", v)
            continue
        raw = os.environ.get(f"NISAR_{section}_{k}".upper())
        out[k] = _coerce(raw, v) if raw is not None else v
    return out


@dataclass
class Settings:
    root: Path
    data_dir: Path
    net: dict = field(default_factory=dict)
    decode: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    search: dict = field(default_factory=dict)

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def chunks_dir(self) -> Path:
        return self.data_dir / "cache" / "chunks"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "cache" / "layout"

    @property
    def catalog_dirs(self) -> list[Path]:
        """shipped (config/catalog, commit it) first, then generated (data/cache/catalog)."""
        return [self.root / "config" / "catalog", self.data_dir / "cache" / "catalog"]

    @property
    def search_dir(self) -> Path:
        return self.data_dir / "cache" / "search"


def load_settings(config_path: str | os.PathLike | None = None, root: Path | None = None) -> Settings:
    root = root or project_root()
    if load_dotenv:
        for p in (root / ".env", Path.cwd() / ".env"):
            if p.exists():
                load_dotenv(p, override=False)
    cfg_file = Path(config_path or os.environ.get("NISAR_CONFIG") or root / "config" / "retrieval.yaml")
    raw = yaml.safe_load(cfg_file.read_text()) if cfg_file.exists() else {}
    data_dir = Path(os.environ.get("NISAR_DATA_DIR") or root / "data").expanduser()
    s = Settings(root=root, data_dir=data_dir)
    for sec in ("net", "decode", "cache", "search"):
        setattr(s, sec, _apply_env(sec, raw.get(sec, {})))
    return s
