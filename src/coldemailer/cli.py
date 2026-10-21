"""Command line: python -m coldemailer <command>."""
from __future__ import annotations

import argparse
import sys

from . import db
from .config import load_settings


def _open(settings, require: bool = True):
    conn = db.connect(settings.db_path)
    if require:
        db.require_initialised(conn)
    return conn


def cmd_init_db(args, settings) -> int:
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    print(f"schema ready at {settings.db_path}")
    return 0


def cmd_seed(args, settings) -> int:
    from . import seed
    result = seed.run(settings, n=args.n, seed=args.seed)
    print(f"seeded {result['contacts']:,} contacts, {result['sends']:,} historical sends, "
          f"{result['events']:,} events in {result['seconds']}s")
    for status, n in sorted(result["counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {status:<18} {n:>7,}")
    return 0


def print_stats(label: str, st) -> None:
    deferred = ", ".join(f"{k} {v:,}" for k, v in sorted(st.deferred.items())) or "none"
    print(f"{label}: sent {st.sent:,} | replies {st.replies:,} | ooo {st.ooo:,} | unsubscribed {st.unsubscribed:,} "
          f"| bounces {st.bounces:,} | soft {st.soft_bounces:,} | held {st.held:,} | deactivated {st.deactivated:,} "
          f"| deferred [{deferred}]")


def cmd_run(args, settings) -> int:
    from . import clock, engine
    conn = _open(settings)
    now = clock.now(conn)
    with clock.run_lock(conn, "cli"):
        st = engine.run_cycle(conn, settings, engine.last_cycle_end(conn) or now, now, owner="cli")
    print_stats(f"cycle at {now}", st)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coldemailer", description="Cold-email outreach engine")
    p.add_argument("--db", help="database path (default from .env or data/coldemailer.db)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init-db", help="create the schema (seed does this for you)")
    s.set_defaults(func=cmd_init_db)

    s = sub.add_parser("seed", help="wipe the database and seed contacts with history")
    s.add_argument("--n", type=int, default=10000)
    s.add_argument("--seed", type=int, default=42)
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("run", help="run one cycle at the current simulated time")
    s.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {"db_path": args.db} if args.db else {}
    settings = load_settings(**overrides)
    try:
        code = args.func(args, settings)
    except db.NotInitialised as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        from .clock import RunLocked
        if isinstance(e, RunLocked):
            print(str(e), file=sys.stderr)
            raise SystemExit(1)
        raise
    raise SystemExit(code or 0)
