"""SQLite connection and transaction helpers."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from importlib import resources


class NotInitialised(Exception):
    """Raised when a command needs a seeded database that doesn't exist."""


def connect(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection, immediate: bool = True):
    """BEGIN [IMMEDIATE] ... COMMIT, ROLLBACK on error. Not re-entrant."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection) -> None:
    sql = resources.files("coldemailer").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)


def is_initialised(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clock'").fetchone()
    if not row:
        return False
    return conn.execute("SELECT 1 FROM clock WHERE id=1").fetchone() is not None


def require_initialised(conn: sqlite3.Connection) -> None:
    if not is_initialised(conn):
        raise NotInitialised("database not initialised — run `python -m coldemailer seed`")


def delete_db_files(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
