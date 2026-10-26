"""debug_hub - one local, live debug layer for retrieval -> processing -> pipeline hand-off.

Run from the repo root:   python tools/debug_hub.py            (or: cd tools && python -m debug_hub)

Unlike tools/sar_debug (which re-implements retrieval so it can stay engine-free), the hub drives the
REAL engine path: engine.retrieval NisarClient -> engine.common P01..P13 -> SceneBundle -> Registry.plan.
It only observes (a recording proxy around the retriever, the chain's own `observer` hook, a metrics poller);
it never changes what the engine does, and it never fabricates data. When something is missing or fails, the
hub shows the engine's own error text.
"""
