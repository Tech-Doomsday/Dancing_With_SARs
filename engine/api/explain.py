"""Plain-language copy for the Background pages, served from config/explain.yaml (the ENGINE owns the words).

Resolution order for the file: NISAR_EXPLAIN_CONFIG, then <NISAR_CONFIG_DIR>/explain.yaml, then the repo's
config/ folder. A missing or corrupt file raises ExplainConfigError with a plain message (the route answers
it, the UI shows a dash) - nothing crashes.

Joined with facts that must never go stale: step names / order / needs come from engine/common/chain.STEPS and
whether a pipeline is modelled (defines run()) is computed live from the registry, never read from YAML.
Every step needs a plain sentence of at most MAX_PLAIN_WORDS words (validated on every load). A layer with no
role, or a role without copy, gets nulls - never a generic sentence."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from engine.common.config import repo_root

MAX_PLAIN_WORDS = 25
_REQUIRED = ("steps", "layers", "pipelines", "nodes")


class ExplainConfigError(RuntimeError):
    pass


def explain_config_path(env: dict | None = None, root: Path | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("NISAR_EXPLAIN_CONFIG"):
        return Path(env["NISAR_EXPLAIN_CONFIG"])
    if env.get("NISAR_CONFIG_DIR"):
        return Path(env["NISAR_CONFIG_DIR"]) / "explain.yaml"
    return (root or repo_root()) / "config" / "explain.yaml"


def load_explain_config(env: dict | None = None, root: Path | None = None) -> dict:
    path = explain_config_path(env, root)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ExplainConfigError(f"explain config not found: {path.name} (set NISAR_EXPLAIN_CONFIG or NISAR_CONFIG_DIR)")
    except Exception as err:
        raise ExplainConfigError(f"explain config {path.name} could not be read: {type(err).__name__}: {err}")
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), dict) for k in _REQUIRED):
        raise ExplainConfigError(f"explain config {path.name} must have mappings: {', '.join(_REQUIRED)}")
    return raw


load_explain = load_explain_config                      # short alias


def _step_rows(cfg: dict, steps) -> list:
    out = []
    for st in steps:
        row = cfg["steps"].get(st.code)
        if not isinstance(row, dict):
            raise ExplainConfigError(f"explain config has no entry for step {st.code}")
        plain = row.get("plain")
        if not isinstance(plain, str) or not plain.strip():
            raise ExplainConfigError(f"explain config: step {st.code} needs a plain sentence")
        if len(plain.split()) > MAX_PLAIN_WORDS:
            raise ExplainConfigError(f"explain config: step {st.code} plain text is over {MAX_PLAIN_WORDS} words")
        out.append({"code": st.code, "name": st.name, "needs": list(st.needs), "plain": plain,
                    "technical": row.get("technical")})
    return out


def explain_payload(registry=None, cfg: dict | None = None, steps=None) -> dict:
    """Everything GET /api/background/explain returns. Step order, counts and `modelled` are live."""
    from engine.common.chain import STEPS
    from engine.registry import Registry
    cfg = cfg or load_explain_config()
    registry = registry or Registry()
    pipelines = {}
    for entry in registry.describe():
        n = entry["name"]
        pipelines[n] = {"code": entry["code"], "plain": cfg["pipelines"].get(n),
                        "applies": entry.get("applies") or None, "modelled": registry.is_modelled(n)}
    layers = {role: {"what_it_is": r.get("what_it_is"), "unit": r.get("unit"), "how_to_read": r.get("how_to_read")}
              for role, r in cfg["layers"].items() if isinstance(r, dict)}
    return {"ok": True, "nodes": cfg["nodes"], "steps": _step_rows(cfg, steps or STEPS), "pipelines": pipelines,
            "layers": layers, "counts": registry.counts()}


def layer_explanation(layer_key: str, cfg: dict | None = None) -> dict:
    """{role, kind, what_it_is, unit, how_to_read} for one flat layer key; no role / no copy -> nulls."""
    from engine.common.layers import parse_layer_key
    cfg = cfg or load_explain_config()
    lid = parse_layer_key(layer_key)
    row = cfg["layers"].get(lid.role) if lid.role else None
    row = row if isinstance(row, dict) else {}
    return {"role": lid.role, "kind": lid.info.kind if lid.info else None, "what_it_is": row.get("what_it_is"),
            "unit": row.get("unit"), "how_to_read": row.get("how_to_read")}
