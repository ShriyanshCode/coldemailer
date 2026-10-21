"""Moving the simulated clock forward, one cycle per step, and running that as a background job."""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime
from typing import Callable

from . import clock, db, engine, timeutil

Progress = Callable[[int, int, engine.CycleStats, str], None]


def default_llm(conn, settings):
    if not settings.openrouter_api_key or not settings.openrouter_models:
        return None
    try:
        from .ai.openrouter import LLMClient
    except ImportError:
        return None
    return LLMClient(conn, settings)


def advance(conn, settings, hours: float, step_hours: float = 1, on_progress: Progress | None = None,
            owner: str | None = None, llm="auto") -> list[engine.CycleStats]:
    """Advance the clock by `hours`, running a cycle per step. hours=0 runs one cycle now.
    `owner` must be unique per process: the run lock lets the same owner re-enter."""
    db.require_initialised(conn)
    owner = owner or f"cli:{os.getpid()}:{threading.get_ident()}"
    if llm == "auto":
        llm = default_llm(conn, settings)
    out: list[engine.CycleStats] = []
    with clock.run_lock(conn, owner):
        now = clock.now(conn)
        if hours <= 0:
            st = engine.run_cycle(conn, settings, engine.last_cycle_end(conn) or now, now, llm, owner)
            if on_progress:
                on_progress(1, 1, st, now)
            return [st]
        total = math.ceil(hours / step_hours)
        for i in range(total):
            step = min(step_hours, hours - i * step_hours)
            prev, now = now, timeutil.add(now, seconds=int(step * 3600))
            clock.set_now(conn, now)
            st = engine.run_cycle(conn, settings, prev, now, llm, owner)
            out.append(st)
            clock.refresh_run_lock(conn, owner)
            if on_progress:
                on_progress(i + 1, total, st, now)
    return out


def start_job(settings, hours: float, step_hours: float = 1) -> int:
    """Run `advance` in a background thread with its own connection. Raises RunLocked if busy."""
    conn = db.connect(settings.db_path)
    db.require_initialised(conn)
    created = datetime.now().isoformat(timespec="seconds")
    job_id = conn.execute("INSERT INTO jobs(kind, status, created_at) VALUES ('advance', 'pending', ?)",
                          (created,)).lastrowid
    owner = f"job:{os.getpid()}:{job_id}"
    if not clock.acquire_run_lock(conn, owner):
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.close()
        raise clock.RunLocked("another run is in progress")
    total = max(1, math.ceil(hours / step_hours)) if hours > 0 else 1
    conn.execute("UPDATE jobs SET status='running', total=? WHERE id=?", (total, job_id))
    conn.close()

    def work():
        c = db.connect(settings.db_path)
        totals = engine.CycleStats()

        def progress(i, n, st, now):
            totals.add(st)
            c.execute("UPDATE jobs SET progress=?, total=? WHERE id=?", (i, n, job_id))

        try:
            advance(c, settings, hours, step_hours, progress, owner)
            result = {**totals.__dict__, "now": clock.now(c)}
            c.execute("UPDATE jobs SET status='done', result_json=?, finished_at=? WHERE id=?",
                      (json.dumps(result), datetime.now().isoformat(timespec="seconds"), job_id))
        except Exception as e:  # surfaced to the UI through the job row
            c.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?",
                      (f"{type(e).__name__}: {e}", datetime.now().isoformat(timespec="seconds"), job_id))
        finally:
            clock.release_run_lock(c, owner)
            c.close()

    threading.Thread(target=work, name=owner, daemon=True).start()
    return job_id


def job(conn, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json")) if d.get("result_json") else None
    return d
