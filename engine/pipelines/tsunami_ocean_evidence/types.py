"""3.17 tsunami_ocean_evidence — pipeline parameters and result dataclasses (own types)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class TsunamiParams:
    """Pipeline parameters. Overrides enter via bundle.request.triggers["tsunami_params"]."""

    # event selection
    min_trigger_magnitude: float = 6.0
    # station selection
    station_radius_km: float = 2000.0
    max_stations: int = 4
    # detection (engine.lib.tsunami.gauges.DetectOptions equivalents)
    trigger_k: float = 5.0
    detide_half_window_h: float = 3.0
    seismic_mask_min: float = 8.0
    # simulation — the heavy part (port design decision 9)
    run_simulation: bool = True
    res_arcmin: int = 2
    sim_hours: float = 2.0
    order: int = 2
    manning_n: float = 0.025
    domain_half_deg: float = 5.0
    sponge_enabled: bool = True
    arrival_threshold_m: float = 0.05
    max_domain_cells: int = 400            # hard cap per axis: the solver grid is capped honestly
    # consistency tolerances (documented diagnostics)
    arrival_tol_min: float = 15.0
    amplitude_factor: float = 3.0

    @classmethod
    def from_triggers(cls, triggers) -> "TsunamiParams":
        overrides = {}
        try:
            raw = dict(triggers or {}).get("tsunami_params") or {}
        except TypeError:
            raw = {}
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        for k, v in dict(raw).items():
            if k in fields and v is not None:
                overrides[k] = v
        return cls(**overrides)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StationDetection:
    """One station's detection outcome (explicit availability state — never a silent null)."""

    station_id: str
    lat: float
    lon: float
    dist_km: float
    kind: str | None = None            # realtime | historical
    availability: dict = field(default_factory=lambda: {"state": "missing", "reason": "not attempted"})
    trigger_ms: float | None = None
    arrival_min: float | None = None
    sigma_noise_m: float | None = None
    max_amplitude_m: float | None = None
    dominant_period_min: float | None = None
    pre_origin_excursion: bool = False
    caveats: list = field(default_factory=list)
    n_samples: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
