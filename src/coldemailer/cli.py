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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coldemailer", description="Cold-email outreach engine")
    p.add_argument("--db", help="database path (default from .env or data/coldemailer.db)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init-db", help="create the schema (seed does this for you)")
    s.set_defaults(func=cmd_init_db)
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
    raise SystemExit(code or 0)
