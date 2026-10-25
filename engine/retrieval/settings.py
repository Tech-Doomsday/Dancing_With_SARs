"""Settings: built-in defaults < config/retrieval.yaml + config/tiling.yaml < .env / NISAR_* env vars.
Defaults live here too, so an old or partial YAML can never crash the engine. No hardcoded paths."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

DEFAULTS: dict[str, dict] = {
    "net": {
        "concurrency": 24, "connect_timeout_s": 20, "stall_timeout_s": 25, "retries": 5,
        "coalesce_gap_bytes": 262144, "max_request_bytes": 33554432,
        "index_block_size_bytes": 524288, "index_page_buffer": False, "block_size_bytes": 4194304,
        "url_ttl_s": 2400, "auth_hosts": ["earthdata.nasa.gov", "earthdatacloud.nasa.gov", "asf.alaska.edu"],
    },
    "decode": {"threads": 4},
    "cache": {
        "ram_chunks_mb": 512, "disk_chunks_gb": 20, "clips_gb": 50,
        "ttl_s": {"granule_clip": None, "search": 21600, "layout_index": None},
        "stale_on_error": True,
    },
    "search": {"max_results": 250, "keep_all_versions": False},
    "poll": {"products": ["GCOV", "GUNW"], "lookback_days": 45, "max_results": 2000, "interval_hours": 6, "region": None},
    "tiling": {"scheme": "mgrs", "cell_size_m": 10000, "margin_m": 300},
}


def project_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for p in [here, *here.parents]:
        if (p / "pyproject.toml").exists() or (p / "config").is_dir():
            return p
    return Path.cwd()


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _coerce(raw: str, like: Any) -> Any:
    if like is None:
        if raw.lower() in ("", "null", "none"):
            return None
        try:
            return float(raw)
        except ValueError:
            return raw
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
    poll: dict = field(default_factory=dict)
    tiling: dict = field(default_factory=dict)

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
    def state_db(self) -> Path:
        return self.data_dir / "state" / "retrieval.sqlite"

    @property
    def catalog_dirs(self) -> list[Path]:
        """shipped (config/catalog, commit it) first, then generated (data/cache/catalog)."""
        return [self.root / "config" / "catalog", self.data_dir / "cache" / "catalog"]

    @property
    def search_dir(self) -> Path:
        return self.data_dir / "cache" / "search"


def _read_yaml(p: Path) -> dict:
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def load_settings(config_path: str | os.PathLike | None = None, root: Path | None = None) -> Settings:
    root = root or project_root()
    if load_dotenv:
        for p in (root / ".env", Path.cwd() / ".env"):
            if p.exists():
                load_dotenv(p, override=False)
    cfg_file = Path(config_path or os.environ.get("NISAR_CONFIG") or root / "config" / "retrieval.yaml")
    raw = _read_yaml(cfg_file)
    raw["tiling"] = _merge(raw.get("tiling") or {}, _read_yaml(root / "config" / "tiling.yaml"))
    merged = _merge(DEFAULTS, raw)
    data_dir = Path(os.environ.get("NISAR_DATA_DIR") or root / "data").expanduser()
    s = Settings(root=root, data_dir=data_dir)
    for sec in DEFAULTS:
        setattr(s, sec, _apply_env(sec, merged[sec]))
    return s
