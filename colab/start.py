"""Notebook entry point:   %run /content/nisar-hazard-engine/colab/start.py

Sets `nc` (the colab driver) in the notebook and runs nc.bootstrap(): .env, dependencies, credentials,
machine tuning, live progress logging. Works wherever the zip was unzipped."""
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
import nisar_colab as nc

nc.REPO = _here.parent
BOOT = nc.bootstrap()
