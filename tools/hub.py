#!/usr/bin/env python3
"""Launcher: python tools/hub.py [--port 8765] [--open] [--allow-selftest]"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from debug_hub.__main__ import main  # noqa: E402

raise SystemExit(main())
