"""
sar_debug - a standalone debug / inspection layer for SAR (NISAR) data retrieval and analysis.

Design rules
------------
* Independent of the hazard engine: nothing in this package imports `engine.*`
  (checked by `python -m sar_debug --selftest`). It can only READ the engine's files on disk.
* Everything optional degrades gracefully: no h5py -> no HDF5 reading, no pyproj -> a built-in
  UTM fallback, no asf_search -> use a saved nisar_results.csv, no login -> metadata-only.
* Output is ONE self-contained HTML report (no server, no internet needed to view it).

Quick start
-----------
    python -m sar_debug --demo --out demo_report.html
    python -m sar_debug --bbox -118.5 33.9 -118.0 34.2 --level GUNW --out report.html
    python -m sar_debug --file some_granule.h5 --out report.html
    python -m sar_debug --npz clip1.npz clip2.npz --engine ./Dancing_With_SARs --out report.html
"""

__version__ = "0.1.0"
