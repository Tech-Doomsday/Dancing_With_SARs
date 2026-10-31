"""The hub page in a real browser, against a live hub server (real gate, routes, queue, store; fake network).
Walks what a person does: open the link, see the tiles, plan, download, open Datasets, see the layers as images,
open one, read a pixel. Skipped where playwright / a chromium build is not installed.
HUB_SHOTS=/some/dir  also writes a screenshot of each step."""
from __future__ import annotations

import importlib.util
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.test_retrieval_stages import _factory                                   # noqa: F401

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("nisar_hub_browser_under_test", ROOT / "colab" / "hub.py")
hub = importlib.util.module_from_spec(_spec)
sys.modules["nisar_hub_browser_under_test"] = hub
_spec.loader.exec_module(hub)

KEY = "browser-test-key-0123456789abcdef"


@pytest.fixture()
def live(monkeypatch, tmp_path):
    import uvicorn
    import engine.api.jobs as api_jobs
    import engine.api.state as api_state
    import engine.common.config as engine_config
    import engine.registry as engine_registry

    monkeypatch.setenv("NISAR_ENABLE_SCANS", "1")
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    monkeypatch.setenv("NISAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NISAR_BACKGROUND_TOKEN", KEY)
    for mod in (engine_config, engine_registry, api_state):
        monkeypatch.setattr(mod, "data_root", lambda: Path(tmp_path))
    api_state.get_runner.cache_clear()
    held: list = []
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: _factory(tmp_path, held))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(hub.KeyGate(hub.build_app(KEY), KEY), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)
    api_state.get_runner.cache_clear()


def test_a_person_can_plan_download_and_browse(live, tmp_path):
    shots = os.environ.get("HUB_SHOTS")

    def shot(page, name):
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(shots) / f"{name}.png"), full_page=True)

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as ex:                                                  # browser binary not installed
            pytest.skip(f"no chromium: {ex}")
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        problems: list = []
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        assert page.goto(live + "/").status == 401                               # no key, no page
        problems.clear()                                                         # that 401 is the point, not a problem
        page.goto(f"{live}/?k={KEY}")
        assert "k=" not in page.url                                              # the key left the address bar
        page.fill("#lat", "34.0"); page.fill("#lon", "-118.5"); page.fill("#size", "30")
        page.wait_for_function("document.querySelector('#cover-table').innerText.includes('11SLT66')")   # debounced preview
        n = page.locator("#map-get polygon.t").count()
        assert 9 <= n <= 16 and f"{n} tile" in page.inner_text("#cover-count")
        assert "11SLT66" in page.inner_text("#cover-table")
        shot(page, "1-preview")

        page.check("#snap")                                                      # one tile, so the fake scene has data
        page.wait_for_function("document.querySelectorAll('#map-get polygon.t').length === 1")
        page.fill("#look", "120")
        page.click("#b-plan")
        page.wait_for_selector("#queue-card:not([hidden])")
        page.wait_for_function("document.querySelector('#queue-table').innerText.includes('planned')", timeout=30000)
        assert "to download" in page.inner_text("#plan-sum")
        shot(page, "2-planned")

        page.click("#plan-sum button")                                           # "Download these ..."
        page.wait_for_function("document.querySelector('#queue-table').innerText.includes('done')", timeout=30000)
        page.wait_for_function("document.querySelector('#cover-table').innerText.includes('granules')", timeout=15000)
        assert "already stored" in page.inner_text("#cover-count")
        shot(page, "3-downloaded")

        page.click("#t-data")
        page.wait_for_selector("#detail figure")
        page.locator("#detail figure").first.scroll_into_view_if_needed()          # thumbnails load as they scroll into view
        page.wait_for_selector("#detail figure img", timeout=30000)              # thumbnails arrive through the token
        assert page.locator("#detail figure img").count() >= 1
        assert page.locator("#ret-list .item").count() >= 1 and "stored" in page.inner_text("#disk-sum")
        assert page.locator("#map-data polygon.t").count() == 1
        shot(page, "4-datasets")

        page.locator("#detail figure").first.click()
        page.wait_for_selector("#viewer img", timeout=30000)
        page.wait_for_selector("#viewer canvas.hist", timeout=30000)
        box = page.locator("#viewer img").bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_function("document.querySelector('#viewer').innerText.includes('row ')", timeout=15000)
        shot(page, "5-viewer")
        page.keyboard.press("Escape")

        with page.expect_download() as dl:
            page.click("#b-zip")
        assert dl.value.suggested_filename.endswith(".zip")
        browser.close()
    assert problems == []
