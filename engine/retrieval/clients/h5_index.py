"""Per-granule layout index (kerchunk-style, but ours): for every gridded layer record shape,
dtype, chunking, filter pipeline and the byte offset/size of every allocated chunk, plus each
grid's x/y axes and EPSG. Built once per granule by a metadata-only pass over HTTP; after that,
any bbox is served by direct range reads of exactly the chunks it touches."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .decode import SUPPORTED
from .fsutil import replace_file

INDEX_VERSION = 3


@dataclass
class LayerLayout:
    path: str
    key: str                      # prototype-compatible flat name: path with '/' -> '__'
    grid: str                     # owning grid group path
    shape: tuple
    dtype: np.dtype
    chunks: tuple | None
    filters: list
    fill: float | None
    origins: np.ndarray           # (n, ndim) element coords of each allocated chunk
    offsets: np.ndarray           # (n,) byte offset in file
    sizes: np.ndarray             # (n,) stored (compressed) byte size
    masks: np.ndarray             # (n,) filter-skip bitmask
    fast: bool
    reason: str = ""
    loaded: bool = True           # False until this layer's chunk table has been read
    _table: np.ndarray | None = field(default=None, repr=False)

    @property
    def table(self) -> np.ndarray:
        """Dense chunk-grid -> allocated-chunk index (-1 = never written, i.e. fill value)."""
        if self._table is None:
            dims = tuple(-(-s // c) for s, c in zip(self.shape, self.chunks))
            t = np.full(dims, -1, dtype=np.int64)
            if len(self.origins):
                t[tuple((self.origins // np.asarray(self.chunks)).T)] = np.arange(len(self.origins))
            self._table = t
        return self._table


@dataclass
class GridLayout:
    path: str
    key: str
    epsg: int
    x: np.ndarray
    y: np.ndarray
    extras: dict = field(default_factory=dict)   # 1-D axes (height cube axis etc.)
    layers: list = field(default_factory=list)
    projection: str | None = None                # HDF5 path of the projection dataset (for the catalog)


@dataclass
class GranuleIndex:
    granule: str
    size: int
    grids: dict
    layers: dict

    def missing_tables(self, paths) -> list[str]:
        return [p for p in paths if not self.layers[p].loaded]

    def ensure_tables(self, h5, paths) -> int:
        """Read chunk tables (byte offsets) for just these layers; everything else stays skeleton."""
        n = 0
        for p in self.missing_tables(paths):
            L = self.layers[p]
            arrs = chunk_table(h5[p])
            L.origins, L.offsets, L.sizes, L.masks = (arrs["origins"], arrs["offsets"], arrs["sizes"], arrs["masks"])
            L._table, L.loaded = None, True
            n += 1
        return n

    # ------------------------------------------------------------ persistence
    def save(self, path: Path):
        arrs, meta = {}, {"v": INDEX_VERSION, "granule": self.granule, "size": self.size,
                          "grids": [], "layers": []}
        for gi, g in enumerate(self.grids.values()):
            arrs[f"g{gi}_x"], arrs[f"g{gi}_y"] = g.x, g.y
            for k, v in g.extras.items():
                arrs[f"g{gi}_e_{k}"] = v
            meta["grids"].append({"path": g.path, "key": g.key, "epsg": g.epsg,
                                  "extras": list(g.extras), "layers": g.layers,
                                  "projection": g.projection})
        for li, l in enumerate(self.layers.values()):
            for n in ("origins", "offsets", "sizes", "masks"):
                arrs[f"l{li}_{n}"] = getattr(l, n)
            dt = l.dtype
            meta["layers"].append({"path": l.path, "key": l.key, "grid": l.grid, "shape": list(l.shape),
                                   "dtype": dt.str if dt.fields is None else dt.descr,
                                   "chunks": list(l.chunks) if l.chunks else None, "filters": l.filters,
                                   "fill": l.fill, "fast": l.fast, "reason": l.reason,
                                   "loaded": l.loaded})
        arrs["meta"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp{os.getpid()}-{threading.get_ident()}.npz")
        np.savez(tmp, **arrs)               # uncompressed: loads in a few ms
        replace_file(tmp, path)

    @staticmethod
    def load(path: Path) -> "GranuleIndex | None":
        try:
            z = np.load(path, allow_pickle=False)
            meta = json.loads(bytes(z["meta"]).decode())
            if meta["v"] != INDEX_VERSION:
                return None
            grids, layers = {}, {}
            for gi, g in enumerate(meta["grids"]):
                grids[g["path"]] = GridLayout(g["path"], g["key"], g["epsg"], z[f"g{gi}_x"], z[f"g{gi}_y"],
                                              {k: z[f"g{gi}_e_{k}"] for k in g["extras"]}, g["layers"],
                                              g.get("projection"))
            for li, l in enumerate(meta["layers"]):
                d = l["dtype"]
                dt = np.dtype(d) if isinstance(d, str) else np.dtype([tuple(x) for x in d])
                layers[l["path"]] = LayerLayout(
                    l["path"], l["key"], l["grid"], tuple(l["shape"]), dt,
                    tuple(l["chunks"]) if l["chunks"] else None, l["filters"], l["fill"],
                    z[f"l{li}_origins"], z[f"l{li}_offsets"], z[f"l{li}_sizes"], z[f"l{li}_masks"],
                    l["fast"], l["reason"], l.get("loaded", True))
            return GranuleIndex(meta["granule"], meta["size"], grids, layers)
        except (OSError, KeyError, ValueError):
            return None


# ------------------------------------------------------------------ building
def read_epsg(node):
    for key in ("epsg_code", "EPSG"):
        if key in node.attrs:
            return int(np.asarray(node.attrs[key]).item())
    try:
        return int(np.asarray(node[()]).item())
    except Exception:
        return None


def find_epsg(grp):
    """-> (epsg, projection dataset path). Walks up from the grid group, then looks below it."""
    node = grp
    while True:
        if "projection" in node:
            v = read_epsg(node["projection"])
            if v:
                return v, node["projection"].name
        if node.name == "/":
            break
        node = node.parent
    found = []
    grp.visititems(lambda n, o: found.append(o) if n.rsplit("/", 1)[-1] == "projection" and hasattr(o, "attrs") else None)
    for o in found:
        v = read_epsg(o)
        if v:
            return v, o.name
    return None, None


def build_index(h5, granule: str, size: int) -> GranuleIndex:
    """Skeleton only: grids, axes, per-layer shape/dtype/chunks/filters. Chunk tables (the bulky,
    scattered part of the metadata) are read per layer on demand via GranuleIndex.ensure_tables."""
    import h5py

    grids_found = []
    h5.visititems(lambda n, o: grids_found.append(o) if isinstance(o, h5py.Group)
                  and "xCoordinates" in o and "yCoordinates" in o else None)
    if not grids_found:
        raise KeyError("no xCoordinates/yCoordinates: not a gridded (L2/L3) product")
    grids, layers, seen, fallback = {}, {}, set(), None
    for g in grids_found:
        epsg, proj = find_epsg(g)
        if epsg is None:
            epsg, proj = fallback if fallback else (None, None)
        elif fallback is None:
            fallback = (epsg, proj)
        x, y = g["xCoordinates"][:], g["yCoordinates"][:]
        if epsg is None or len(x) == 0 or len(y) == 0:
            continue
        ny, nx = len(y), len(x)
        cands = []
        g.visititems(lambda n, o: cands.append(o) if isinstance(o, h5py.Dataset) and o.ndim in (2, 3)
                     and o.shape[-2:] == (ny, nx) and o.name not in seen else None)
        if not cands:
            continue
        gl = GridLayout(g.name, g.name.strip("/").replace("/", "__"), int(epsg), x, y, projection=proj)
        if any(d.ndim == 3 for d in cands):
            for k, v in g.items():
                if (isinstance(v, h5py.Dataset) and v.ndim == 1 and k not in ("xCoordinates", "yCoordinates")
                        and v.size <= 4096):
                    gl.extras[k] = v[:]
        for d in cands:
            seen.add(d.name)
            layers[d.name] = _layer(d, gl)
            gl.layers.append(d.name)
        grids[g.name] = gl
    if not layers:
        raise ValueError("file has no gridded layers")
    return GranuleIndex(granule, size, grids, layers)


def _layer(d, gl: GridLayout) -> LayerLayout:
    dt = d.dtype
    key = d.name.strip("/").replace("/", "__")
    fill = None
    try:
        fv = d.fillvalue
        fill = float(fv) if dt.fields is None and dt.kind in "fiub" else None
    except Exception:
        pass
    empty = dict(origins=np.zeros((0, d.ndim), np.int64), offsets=np.zeros(0, np.int64),
                 sizes=np.zeros(0, np.int64), masks=np.zeros(0, np.uint32))
    if d.chunks is None:
        return LayerLayout(d.name, key, gl.path, d.shape, dt, None, [], fill, fast=False,
                           reason="contiguous layout", **empty)
    pl = d.id.get_create_plist()
    filters = [[int(pl.get_filter(i)[0]), [int(v) for v in pl.get_filter(i)[2]]] for i in range(pl.get_nfilters())]
    if dt.hasobject or dt.kind in "OSUV" and dt.fields is None:
        return LayerLayout(d.name, key, gl.path, d.shape, dt, d.chunks, filters, fill, fast=False,
                           reason="non-numeric dtype", **empty)
    bad = [f[0] for f in filters if f[0] not in SUPPORTED]
    if bad:
        return LayerLayout(d.name, key, gl.path, d.shape, dt, d.chunks, filters, fill, fast=False,
                           reason=f"unsupported HDF5 filter(s) {bad}", **empty)
    return LayerLayout(d.name, key, gl.path, d.shape, dt, d.chunks, filters, fill, fast=True,
                       loaded=False, **empty)


def chunk_table(d) -> dict:
    n = d.id.get_num_chunks()
    infos = [d.id.get_chunk_info(i) for i in range(n)]
    return dict(
        origins=np.array([ci.chunk_offset for ci in infos], dtype=np.int64).reshape(n, d.ndim),
        offsets=np.array([ci.byte_offset for ci in infos], dtype=np.int64),
        sizes=np.array([ci.size for ci in infos], dtype=np.int64),
        masks=np.array([ci.filter_mask for ci in infos], dtype=np.uint32))


# ------------------------------------------------------------------ catalog + targeted access
def catalog_of(ix: GranuleIndex) -> dict:
    """What a full walk found, reusable for every later granule of the same product type:
    lets us open datasets by path instead of walking the whole file."""
    return {"v": 1, "grids": [{"path": g.path, "projection": g.projection, "extras": list(g.extras),
                               "layers": list(g.layers)} for g in ix.grids.values()]}


def catalog_missing(ix: GranuleIndex, catalog: dict, pat) -> list[str]:
    return [p for gd in catalog["grids"] for p in gd["layers"]
            if p not in ix.layers and (pat is None or pat.search(p))]


def extend_index(ix: GranuleIndex, h5, catalog: dict, pat) -> int:
    """Add skeleton entries for catalog layers matching `pat`, touching only those objects.
    Raises KeyError if the file no longer matches the catalog (caller falls back to a full walk)."""
    n = 0
    for gd in catalog["grids"]:
        want = [p for p in gd["layers"] if p not in ix.layers and (pat is None or pat.search(p))]
        if not want:
            continue
        gl = ix.grids.get(gd["path"])
        if gl is None:
            g = h5[gd["path"]]
            x, y = g["xCoordinates"][:], g["yCoordinates"][:]
            epsg = read_epsg(h5[gd["projection"]]) if gd.get("projection") else None
            if epsg is None:
                raise KeyError("projection missing")
            gl = GridLayout(g.name, g.name.strip("/").replace("/", "__"), int(epsg), x, y,
                            projection=gd.get("projection"))
            for k in gd["extras"]:
                gl.extras[k] = g[k][:]
            ix.grids[gd["path"]] = gl
        for p in want:
            d = h5[p]
            if d.shape[-2:] != (len(gl.y), len(gl.x)):
                raise KeyError(f"shape mismatch at {p}")
            ix.layers[p] = _layer(d, gl)
            gl.layers.append(p)
            n += 1
    return n
