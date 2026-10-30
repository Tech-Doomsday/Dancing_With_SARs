"""SQLite opening must survive filesystems where WAL's -shm file fails ("disk I/O error")."""
import shutil
import sqlite3

import pytest

from engine.common import sqlite_util as su


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("NISAR_SQLITE_WAL", "NISAR_DB_DIR"):
        monkeypatch.delenv(k, raising=False)
    su._resolve.cache_clear()


def test_default_is_rollback_journal(tmp_path):
    c = su.connect(tmp_path / "a.db")
    c.execute("CREATE TABLE t(x)"); c.commit()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    c.close()
    assert not (tmp_path / "a.db-shm").exists()


def test_wal_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("NISAR_SQLITE_WAL", "1")
    c = su.connect(tmp_path / "w.db")
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    c.close()


def _make_wal_db_with_unflushed_wal(path):
    """A WAL-flagged db whose newest rows exist ONLY in the -wal file (as after a crash)."""
    live = sqlite3.connect(path)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("CREATE TABLE t(x)")
    live.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(50)])
    live.commit()
    snap = path.with_name("snap.db")
    shutil.copy(path, snap)
    shutil.copy(str(path) + "-wal", str(snap) + "-wal")
    live.close()
    return snap


def test_stuck_wal_file_is_healed_and_data_survives(tmp_path):
    snap = _make_wal_db_with_unflushed_wal(tmp_path / "live.db")
    assert su._is_wal_file(snap)
    c = su.connect(snap)
    assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    c.close()
    assert not su._is_wal_file(snap)


def test_io_error_on_mode_switch_triggers_heal_and_retry(tmp_path, monkeypatch):
    snap = _make_wal_db_with_unflushed_wal(tmp_path / "live.db")
    real = su._apply_mode
    calls = {"n": 0}

    def flaky(conn, path, want_wal):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return real(conn, path, want_wal)

    monkeypatch.setattr(su, "_apply_mode", flaky)
    c = su.connect(snap)
    assert calls["n"] == 2
    assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    c.close()


def test_persistent_io_error_gives_actionable_message(tmp_path, monkeypatch):
    def always(conn, path, want_wal):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(su, "_apply_mode", always)
    with pytest.raises(sqlite3.OperationalError, match="NISAR_DB_DIR"):
        su.connect(tmp_path / "x.db")


def test_resolve_prefers_env_then_default_then_local_fallback(tmp_path, monkeypatch):
    default = tmp_path / "data"
    assert su.resolve_db_dir(default) == default
    su._resolve.cache_clear()
    chosen = tmp_path / "elsewhere"
    monkeypatch.setenv("NISAR_DB_DIR", str(chosen))
    assert su.resolve_db_dir(default) == chosen
    # unusable folder (a regular file where a directory must be) -> per-user local fallback
    su._resolve.cache_clear()
    monkeypatch.delenv("NISAR_DB_DIR")
    blocker = tmp_path / "blocker"; blocker.write_text("x")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    got = su.resolve_db_dir(blocker / "data")
    assert got == tmp_path / "local" / "nisar-hazard-engine" / "db"
