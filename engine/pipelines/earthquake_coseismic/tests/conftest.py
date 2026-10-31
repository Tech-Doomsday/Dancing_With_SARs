"""Put the repo (or skeleton) root on sys.path so `engine.*` imports resolve
when pytest is invoked from any working directory."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
for _p in (_ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)
