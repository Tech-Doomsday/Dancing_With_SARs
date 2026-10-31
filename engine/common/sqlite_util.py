"""Safe SQLite opening for every engine database (verdicts.db, jobs.sqlite, retrieval.sqlite).

Why this exists: `PRAGMA journal_mode=WAL` needs a memory-mapped `-shm` side file. On some Windows
setups (exFAT/FAT32 or mapped drives, ReFS "Dev Drive", cloud-sync folders, antivirus hooks) that
mapping fails with `sqlite3.OperationalError: disk I/O error` - and a failed attempt can leave the
database file *flagged* as WAL, so every later open fails too, even with a try/except around the PRAGMA.

So the engine now uses SQLite's default rollback journal (no -shm file, works on every filesystem; the
engine's write volume is tiny) and heals files that an older build left stuck in WAL mode.

Configuration (all optional, set in .env - nothing is hardcoded to a folder):
    NISAR_SQLITE_WAL=1   opt in to WAL (only if your filesystem is known-good)
    NISAR_DB_DIR=<dir>   keep the .db/.sqlite files in this folder instead of <data dir>
If the chosen folder cannot hold a SQLite file at all, `resolve_db_dir` falls back to a per-user local
folder (%LOCALAPPDATA%\\nisar-hazard-engine\\db on Windows) and logs a warning.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("nisar.sqlite")

_TRUE = ("1", "true", "yes", "on")


def wal_requested() -> bool:
    return os.environ.get("NISAR_SQLITE_WAL", "").strip().lower() in _TRUE


def _is_wal_file(path: Path) -> bool:
    """The db header (bytes 18-19 = file-format read/write version) is 2 when the file is flagged WAL."""
    try:
        with open(path, "rb") as f:
            head = f.read(20)
        return len(head) >= 20 and head[18] == 2 and head[19] == 2
    except OSError:
        return False


def _is_io_error(ex: BaseException) -> bool:
    return isinstance(ex, sqlite3.OperationalError) and "i/o" in str(ex).lower()


def _heal(path: Path) -> None:
    """Convert a WAL-flagged file back to a rollback journal WITHOUT the shared-memory file.
    EXCLUSIVE locking mode before the first access means SQLite never touches -shm (documented
    behaviour); the WAL contents are folded into the main file when the mode switches."""
    c = sqlite3.connect(path, timeout=30)
    try:
        c.execute("PRAGMA locking_mode=EXCLUSIVE")
        c.execute("PRAGMA journal_mode=DELETE")
    finally:
        c.close()


def _apply_mode(conn: sqlite3.Connection, path: Path, want_wal: bool) -> None:
    if want_wal:
        conn.execute("PRAGMA journal_mode=WAL")
    elif _is_wal_file(path):
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()   # force a real read now, not later


def _diagnose(path: Path) -> str:
    sibs = []
    try:
        for p in sorted(path.parent.glob(path.name + "*")):
            sibs.append(f"{p.name} ({p.stat().st_size} B)")
    except OSError:
        pass
    return (f"cannot use SQLite at {path} (folder exists={path.parent.exists()}; "
            f"files: {', '.join(sibs) or 'none'}). Fix: set NISAR_DB_DIR in .env to a folder on a normal local "
            f"NTFS drive (e.g. under %LOCALAPPDATA%), make sure no other process (a second engine, DB Browser, "
            f"a sync client) has the file open, and delete a leftover '{path.name}-wal' / '-shm' / '-journal' only "
            f"if the database is disposable.")


def connect(path: str | os.PathLike, *, timeout: float = 30.0, **kw) -> sqlite3.Connection:
    """sqlite3.connect() with the journal mode handled safely. Extra kwargs go to sqlite3.connect."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    want_wal = wal_requested()
    last: Exception | None = None
    for attempt in range(3):
        conn = sqlite3.connect(path, timeout=timeout, **kw)
        try:
            _apply_mode(conn, path, want_wal)
            return conn
        except sqlite3.OperationalError as ex:
            conn.close()
            last = ex
            if not _is_io_error(ex):
                raise
            log.warning("sqlite I/O error on %s (%s); attempt %d: falling back to rollback journal",
                        path, ex, attempt + 1)
            want_wal = False
            try:
                _heal(path)
            except (sqlite3.Error, OSError) as hex_:
                log.warning("could not heal %s: %s", path, hex_)
    raise sqlite3.OperationalError(f"{last} - {_diagnose(path)}") from last


def _probe(folder: Path) -> None:
    """Prove `folder` can host a SQLite file (create, write, commit, read back)."""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / ".probe.sqlite"
    conn = connect(p)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.execute("DELETE FROM t")
        conn.commit()
    finally:
        conn.close()


def _local_fallback() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "nisar-hazard-engine" / "db"


@lru_cache(maxsize=32)
def _resolve(configured: str, default: str) -> Path:
    first = Path(configured).expanduser() if configured else Path(default)
    try:
        _probe(first)
        return first
    except (sqlite3.Error, OSError) as ex:
        fb = _local_fallback()
        log.warning("SQLite is unusable in %s (%s); using %s instead. Set NISAR_DB_DIR to choose a folder.",
                    first, ex, fb)
        _probe(fb)          # if even this fails, let the error surface - nothing more we can do
        return fb


def resolve_db_dir(default: str | os.PathLike) -> Path:
    """Folder that holds verdicts.db / jobs.sqlite: NISAR_DB_DIR, else `default` (the data dir),
    else a per-user local folder if the first choice cannot host a SQLite file."""
    return _resolve(os.environ.get("NISAR_DB_DIR", "").strip(), str(default))
