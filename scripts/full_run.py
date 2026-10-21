"""Full-scale run: seed N contacts, advance D simulated days, verify, and write a results file.

    python scripts/full_run.py --n 10000 --days 60 --out docs/results/run-10k.md
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import time

from coldemailer import db, report, runner, seed, timeutil, verify
from coldemailer.config import load_settings
from coldemailer.engine import CycleStats


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=10000)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--db", default="data/full_run.db")
    p.add_argument("--out", required=True)
    p.add_argument("--no-ai", action="store_true", help="ignore OPENROUTER_API_KEY for this run")
    args = p.parse_args()

    overrides = {"db_path": args.db}
    if args.no_ai:
        overrides.update(openrouter_api_key=None, openrouter_models=())
    s = load_settings(**overrides)

    t0 = time.perf_counter()
    seeded = seed.run(s, n=args.n, seed=args.seed)
    t_seed = time.perf_counter() - t0
    print(f"seeded {args.n:,} in {t_seed:.1f}s", flush=True)

    conn = db.connect(s.db_path)
    days, totals = [], CycleStats()
    t1 = time.perf_counter()
    for d in range(args.days):
        ts = time.perf_counter()
        st = CycleStats()
        for x in runner.advance(conn, s, 24):
            st.add(x)
        totals.add(st)
        day = timeutil.ist_day(timeutil.add(conn.execute("SELECT now FROM clock").fetchone()[0], seconds=-1))
        days.append((day, st, time.perf_counter() - ts))
        print(f"day {d + 1:>3} ({day}): sent {st.sent:>5,}  replies {st.replies:>4}  ooo {st.ooo:>3}  "
              f"bounces {st.bounces:>3}  deactivated {st.deactivated:>4}  {time.perf_counter() - ts:5.1f}s", flush=True)
    t_run = time.perf_counter() - t1

    t2 = time.perf_counter()
    v = verify.run(conn, s)
    t_verify = time.perf_counter() - t2
    r = report.overview(conn, s, timeutil.ist_day(s.clock_start), None)
    ai = report.ai_usage(conn, s)
    k = r["kpis"]

    lines = [
        f"# Full run: {args.n:,} contacts, {args.days} simulated days", "",
        f"Command: `python scripts/full_run.py --n {args.n} --days {args.days}{' --no-ai' if args.no_ai else ''}`  ",
        f"Machine: {platform.platform()}, {platform.processor() or platform.machine()}, {os.cpu_count()} logical CPUs, "
        f"Python {platform.python_version()}, SQLite {db.sqlite3.sqlite_version}  ",
        f"AI: {'OpenRouter ' + ' → '.join(s.openrouter_models) if s.openrouter_api_key else 'off (static/committed templates + rules)'}", "",
        "## Time taken", "",
        "| Phase | Wall time |", "|---|---|",
        f"| Seed {args.n:,} contacts with history | {t_seed:.1f}s |",
        f"| Advance {args.days} days ({args.days * 24} hourly cycles) | {t_run:.1f}s ({t_run / args.days:.2f}s per simulated day) |",
        f"| Verify (12 invariant checks) | {t_verify:.1f}s |", "",
        "## Final counts", "",
        "| Status | Contacts | Share |", "|---|---:|---:|",
        *[f"| {x['label']} | {x['count']:,} | {x['count'] / k['total']:.1%} |" for x in r["statuses"]],
        f"| **Total** | **{k['total']:,}** | |", "",
        f"- Emails sent during the run: **{totals.sent:,}** (plus {seeded['sends']:,} imported history sends)",
        f"- Replies {totals.replies:,} · out-of-office {totals.ooo:,} (sequence continued) · unsubscribed {totals.unsubscribed:,} · "
        f"hard bounces {totals.bounces:,} · soft bounces {totals.soft_bounces:,} · put on hold for review {totals.held:,} · "
        f"deactivated {totals.deactivated:,}",
        f"- Reply rate {k['reply_rate']:.1%} ({k['replied']:,} of {k['contacted']:,} contacted, OOO excluded) · "
        f"bounce rate {k['bounce_rate']:.1%} over the run",
        f"- Open review items: {k['needs_review']:,} · on hold: {r['markers']['on_hold']:,} · suppression list: {r['suppression']['total']:,}",
        f"- Paused mailboxes at the end: {conn.execute('SELECT COUNT(*) FROM mailboxes WHERE paused=1').fetchone()[0]} of "
        f"{conn.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0]}",
        f"- Classifier: " + ", ".join(f"{a} {b:,}" for a, b in r["classifiers"].items())
        + (f"; accuracy vs ground truth {r['classifier_accuracy']['rate']:.1%} of {r['classifier_accuracy']['checked']:,}"
           if r["classifier_accuracy"]["checked"] else ""),
        f"- LLM calls {ai['totals']['calls']:,}: ok {ai['totals']['successes']:,}, 429s {ai['totals']['rate_limited']:,}, "
        f"answered by a fallback model {ai['totals']['fallback_next_model']:,}", "",
        f"## Verify: {'PASS' if v['ok'] else 'FAIL'} ({sum(c['ok'] for c in v['checks'])}/{len(v['checks'])})", "",
        *[f"- {'✅' if c['ok'] else '❌'} {c['name']}" + ("" if c["ok"] else f": {c['detail']}") for c in v["checks"]], "",
        "## Sends per simulated day", "",
        "| Day (IST) | Sent | Replies | OOO | Bounces | Deactivated | Wall time |", "|---|---:|---:|---:|---:|---:|---:|",
        *[f"| {d} | {st.sent:,} | {st.replies} | {st.ooo} | {st.bounces} | {st.deactivated:,} | {secs:.1f}s |"
          for d, st, secs in days],
    ]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {args.out}; verify {'PASS' if v['ok'] else 'FAIL'}; total {time.perf_counter() - t0:.0f}s")
    return 0 if v["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
