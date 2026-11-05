"""The UI (another origin than the engine) reads X-Img-Meta off the preview response; the browser hides
every non-safelisted response header unless CORS exposes it. Without it the SAR layer says
'the engine returned no image corners' even though the engine sent them."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.main import create_app  # noqa: E402


def test_cors_exposes_the_headers_the_ui_reads():
    app = create_app()

    @app.get("/__t/img")
    def _img():
        return Response(b"x", media_type="image/png", headers={"X-Img-Meta": "{}", "Retry-After": "3"})

    origin = "http://localhost:3000"
    r = TestClient(app).get("/__t/img", headers={"Origin": origin})
    assert r.headers.get("access-control-allow-origin") == origin
    exposed = {h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")}
    assert {"x-img-meta", "retry-after"} <= exposed
