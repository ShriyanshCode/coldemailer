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
    from . import clock, runner
    conn = _open(settings)
    st = runner.advance(conn, settings, 0)[0]
    print_stats(f"cycle at {clock.now(conn)}", st)
    return 0


def cmd_advance(args, settings) -> int:
    import time
    from . import engine, runner, timeutil
    conn = _open(settings)
    hours = args.days * 24 + args.hours
    if hours <= 0:
        print("nothing to do: pass --days or --hours", file=sys.stderr)
        return 1
    t0 = time.perf_counter()
    day_stats: dict[str, engine.CycleStats] = {}
    totals = engine.CycleStats()

    def progress(i, n, st, now):
        day = timeutil.ist_day(timeutil.add(now, seconds=-1))
        day_stats.setdefault(day, engine.CycleStats()).add(st)
        totals.add(st)
        if i == n or timeutil.ist_day(now) != day:
            print_stats(f"{day}", day_stats[day])

    runner.advance(conn, settings, hours, args.step, progress)
    print_stats(f"total {hours:g}h in {time.perf_counter() - t0:.1f}s", totals)
    return 0


def cmd_review_list(args, settings) -> int:
    conn = _open(settings)
    rows = conn.execute(
        "SELECT r.*, c.email FROM review_items r JOIN contacts c ON c.id=r.contact_id "
        "WHERE r.resolved_at IS NULL ORDER BY r.id").fetchall()
    for r in rows:
        print(f"#{r['id']:<6} {r['kind']:<14} {r['reason']:<22} {r['email']:<40} {r['created_at']}")
    print(f"{len(rows)} open item(s)")
    return 0


def cmd_review_resolve(args, settings) -> int:
    from . import actions
    conn = _open(settings)
    actions.resolve_review(conn, args.id, args.action, args.days, args.note, settings)
    print(f"review item #{args.id}: {args.action}")
    return 0


def cmd_mailbox_resume(args, settings) -> int:
    from . import actions
    conn = _open(settings)
    actions.resume_mailbox(conn, args.id)
    print(f"mailbox {args.id} resumed")
    return 0


def cmd_suppress(args, settings) -> int:
    from . import actions
    conn = _open(settings)
    row = conn.execute("SELECT id FROM contacts WHERE email=?", (args.email,)).fetchone()
    if row is None:
        print(f"no contact with email {args.email}", file=sys.stderr)
        return 1
    actions.suppress_contact(conn, row["id"])
    print(f"{args.email} suppressed")
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

    s = sub.add_parser("advance", help="move the simulated clock forward, running a cycle per step")
    s.add_argument("--days", type=float, default=0)
    s.add_argument("--hours", type=float, default=0)
    s.add_argument("--step", type=float, default=1, help="hours per cycle (default 1)")
    s.set_defaults(func=cmd_advance)

    review = sub.add_parser("review", help="the human review queue").add_subparsers(dest="sub", required=True)
    s = review.add_parser("list", help="open review items")
    s.set_defaults(func=cmd_review_list)
    s = review.add_parser("resolve", help="resolve an item")
    s.add_argument("id", type=int)
    s.add_argument("action", help="retry_now | retry_later | mark_bounced | suppress | label:<label>")
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--note")
    s.set_defaults(func=cmd_review_resolve)

    mailbox = sub.add_parser("mailbox", help="mailbox actions").add_subparsers(dest="sub", required=True)
    s = mailbox.add_parser("resume", help="resume a paused mailbox")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_mailbox_resume)

    s = sub.add_parser("suppress", help="add a contact's email to the suppression list")
    s.add_argument("email")
    s.set_defaults(func=cmd_suppress)
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
        if isinstance(e, (RunLocked, ValueError)):
            print(str(e), file=sys.stderr)
            raise SystemExit(1)
        raise
    raise SystemExit(code or 0)
