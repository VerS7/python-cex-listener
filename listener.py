#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from cex_listener.config import ListenerConfig
from cex_listener.runner import export_events, print_json, run_loop, run_once, status, with_overrides


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run TradingView CEX listener.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    once = subparsers.add_parser("once", help="Fetch one snapshot, diff it against the DB baseline, and exit.")
    _add_runtime_args(once)

    run = subparsers.add_parser("run", help="Continuously poll TradingView and persist events.")
    _add_runtime_args(run)

    status_parser = subparsers.add_parser("status", help="Print listener DB status.")
    status_parser.add_argument("--db", default="cex_listener.db", help="SQLite DB path.")

    export_parser = subparsers.add_parser("export-events", help="Export saved events to CSV.")
    export_parser.add_argument("--db", default="cex_listener.db", help="SQLite DB path.")
    export_parser.add_argument("-o", "--output", default="events.csv", help="CSV output path.")

    return parser.parse_args()


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="listener_config.json", help="Listener JSON config path.")
    parser.add_argument("--interval", type=float, help="Override poll interval seconds.")
    parser.add_argument("--limit", type=int, help="Override max scanner rows. 0 means all rows.")
    parser.add_argument("--db", help="Override SQLite DB path.")
    parser.add_argument("--events-jsonl", help="Override events JSONL path.")
    parser.add_argument("--emit-initial-created", action="store_true", help="Emit created events on first baseline run.")
    parser.add_argument("--verbose", action="store_true", help="Print snapshot summaries.")


def load_config(args: argparse.Namespace) -> ListenerConfig:
    try:
        config = ListenerConfig.load(args.config)
    except FileNotFoundError:
        if args.config != "listener_config.json":
            raise
        config = ListenerConfig()
    return with_overrides(
        config,
        interval=args.interval,
        limit=args.limit,
        output_db=args.db,
        events_jsonl=args.events_jsonl,
        emit_initial_created=True if args.emit_initial_created else None,
    )


def main() -> int:
    args = parse_args()
    try:
        if args.command == "once":
            run_once(load_config(args), verbose=args.verbose)
            return 0
        if args.command == "run":
            run_loop(load_config(args), verbose=args.verbose)
            return 0
        if args.command == "status":
            print_json(status(args.db))
            return 0
        if args.command == "export-events":
            count = export_events(args.db, args.output)
            print(f"exported {count} events -> {args.output}")
            return 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

