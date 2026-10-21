"""The simulated clock and the run lock.

Business logic only ever asks `now(conn)`. The run lock is the one place wall-clock
time is used, because it protects real OS processes (CLI vs API job) from each other.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from . import db


class ClockError(Exception):
    pass


class RunLocked(Exception):
    pass


def now(conn) -> str:
    row = conn.execute("SELECT now FROM clock WHERE id=1").fetchone()
    if row is None:
        raise db.NotInitialised("database not initialised — run `python -m coldemailer seed`")
    return row["now"]


def started_at(conn) -> str:
    return conn.execute("SELECT started_at FROM clock WHERE id=1").fetchone()["started_at"]


def init(conn, ts: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO clock(id, now, started_at, updated_at) VALUES (1, ?, ?, ?)",
        (ts, ts, ts),
    )


def set_now(conn, ts: str) -> None:
    current = now(conn)
    if ts < current:
        raise ClockError("clock cannot move backwards")
    conn.execute("UPDATE clock SET now=?, updated_at=? WHERE id=1", (ts, ts))


def acquire_run_lock(conn, owner: str, ttl_s: int = 600) -> bool:
    wall = time.time()
    with db.tx(conn):
        conn.execute("INSERT OR IGNORE INTO run_lock(id, owner, expires_at) VALUES (1, NULL, 0)")
        cur = conn.execute(
            "UPDATE run_lock SET owner=?, expires_at=? "
            "WHERE id=1 AND (owner IS NULL OR expires_at < ? OR owner=?)",
            (owner, wall + ttl_s, wall, owner),
        )
        return cur.rowcount == 1


def refresh_run_lock(conn, owner: str, ttl_s: int = 600) -> None:
    conn.execute("UPDATE run_lock SET expires_at=? WHERE id=1 AND owner=?", (time.time() + ttl_s, owner))


def release_run_lock(conn, owner: str) -> None:
    conn.execute("UPDATE run_lock SET owner=NULL, expires_at=0 WHERE id=1 AND owner=?", (owner,))


def lock_holder(conn) -> str | None:
    row = conn.execute("SELECT owner, expires_at FROM run_lock WHERE id=1").fetchone()
    if row and row["owner"] and row["expires_at"] > time.time():
        return row["owner"]
    return None


@contextmanager
def run_lock(conn, owner: str, ttl_s: int = 600):
    if not acquire_run_lock(conn, owner, ttl_s):
        raise RunLocked("another run is in progress")
    try:
        yield
    finally:
        release_run_lock(conn, owner)
