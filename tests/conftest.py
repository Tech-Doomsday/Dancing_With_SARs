import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from engine.retrieval.auth.secrets import EarthdataAuth
from engine.retrieval.clients.nisar_client import NisarClient
from engine.retrieval.clients.types import GranuleRef
from engine.retrieval.settings import load_settings
from synth import RangeServer, make_granule


@pytest.fixture(scope="session")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("served")
    make_granule(root / "NISAR_L2_PR_GUNW_A_001_000_000_000_000_000_000_20260701T000000_20260701T000030_X05010_N_F_J_001.h5")
    return root


@pytest.fixture()
def server(served):
    s = RangeServer(served)
    yield s


_CREATED: list = []


def make_client(tmp_path, **net):
    cfg = load_settings(root=Path(__file__).resolve().parents[1])
    cfg.data_dir = tmp_path / "data"
    cfg.net.update(auth_hosts=["127.0.0.1"], retries=3, connect_timeout_s=5, stall_timeout_s=10, **net)
    c = NisarClient(cfg, EarthdataAuth(token="test"))
    _CREATED.append(c)
    return c


@pytest.fixture(autouse=True)
def _close_all_clients():
    """Every client a test creates is closed afterwards, even if the test failed half-way."""
    yield
    while _CREATED:
        try:
            _CREATED.pop().close()
        except Exception:
            pass


def rm(path):
    """Delete a directory tree for real. Windows can hold freshly written files for a moment
    (antivirus / indexer), so retry, and fail loudly instead of silently leaving data behind."""
    import shutil
    import time
    path = Path(path)
    for i in range(40):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.1 * (1 + i // 5))
    raise AssertionError(f"could not delete {path} (files locked by another process?)")


@pytest.fixture()
def client(tmp_path):
    c = make_client(tmp_path)
    yield c
    c.close()


@pytest.fixture()
def granule(server, served):
    name = next(served.glob("*.h5")).name
    return GranuleRef(name[:-3], server.url(name), "GUNW")
