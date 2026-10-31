"""HDF5 chunk filter pipeline, decoded without libhdf5 (so no global lock, threads scale).
Supports deflate (1), shuffle (2), fletcher32 (3). Anything else => UnsupportedFilter and the
layer is served by the h5py fallback instead."""
from __future__ import annotations

import zlib

import numpy as np

DEFLATE, SHUFFLE, FLETCHER32 = 1, 2, 3
SUPPORTED = {DEFLATE, SHUFFLE, FLETCHER32}


class UnsupportedFilter(Exception):
    pass


def unshuffle(buf, itemsize: int) -> bytes:
    a = np.frombuffer(buf, dtype=np.uint8)
    n = a.size // itemsize
    head = a[: n * itemsize].reshape(itemsize, n).T.reshape(-1)
    return head.tobytes() + a[n * itemsize:].tobytes()


def decode_chunk(buf, filters: list, mask: int, dtype: np.dtype, shape: tuple) -> np.ndarray:
    """filters: [[id, cd_values], ...] in write order. `mask` bit i set => filter i was skipped."""
    b = bytes(buf) if not filters else buf   # unfiltered: detach from the shared network buffer
    for i in range(len(filters) - 1, -1, -1):
        if (mask >> i) & 1:
            continue
        fid = filters[i][0]
        if fid == DEFLATE:
            b = zlib.decompress(b)
        elif fid == SHUFFLE:
            b = unshuffle(b, dtype.itemsize)
        elif fid == FLETCHER32:
            b = bytes(b)[:-4]
        else:
            raise UnsupportedFilter(f"HDF5 filter id {fid}")
    return np.frombuffer(b, dtype=dtype).reshape(shape)
