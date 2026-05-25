#!/usr/bin/env python3
"""
Export TradingView CEX Screener rows to CSV.

The HTTP/pagination logic is shared with the listener package so the batch
exporter and continuous listener stay on the same TradingView scanner contract.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from cex_listener.client import TradingViewCexClient, client_config_from_namespace, parse_columns


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export ru.tradingview.com/cex-screener table to CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-o", "--output", default="tradingview_cex.csv", help="CSV output path.")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to export. 0 means all rows.")
    parser.add_argument("--page-size", type=int, default=1000, help="Rows per scanner request.")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between paginated requests.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=3, help="Retries per scanner request.")
    parser.add_argument("--delimiter", default=",", help="CSV delimiter.")
    parser.add_argument("--encoding", default="utf-8-sig", help="CSV encoding.")
    parser.add_argument("--lang", default="ru", help="TradingView scanner language option.")

    parser.add_argument("--exchange", action="append", default=[], help="Exchange filter. Can be repeated or comma-separated.")
    parser.add_argument(
        "--type",
        action="append",
        default=[],
        help="Market type filter: spot, swap, futures, perpetual. Can be repeated or comma-separated.",
    )
    parser.add_argument("--base", action="append", default=[], help="Base currency filter, e.g. BTC.")
    parser.add_argument("--quote", action="append", default=[], help="Quote currency filter, e.g. USDT.")
    parser.add_argument("--ticker", action="append", default=[], help="TradingView ticker id, e.g. BINANCE:BTCUSDT.")

    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--min-change", type=float, help="Minimum 24h change percent.")
    parser.add_argument("--max-change", type=float, help="Maximum 24h change percent.")
    parser.add_argument("--min-volume", type=float, help="Minimum 24h volume.")
    parser.add_argument("--max-volume", type=float, help="Maximum 24h volume.")
    parser.add_argument("--min-volume-change", type=float, help="Minimum 24h volume change percent.")
    parser.add_argument("--max-volume-change", type=float, help="Maximum 24h volume change percent.")

    parser.add_argument(
        "--filter-json",
        help=(
            "Extra TradingView filter2 JSON. Use an expression object, an operands list, "
            "a full filter2 object, or @path/to/file.json."
        ),
    )
    parser.add_argument(
        "--columns",
        help=(
            "Comma-separated API columns/aliases. Defaults to the current CEX overview columns. "
            "Aliases include ticker, price, volume_24h, change_24h_pct."
        ),
    )
    parser.add_argument("--sort-by", default="24h_vol|5", help="TradingView API column to sort by.")
    parser.add_argument("--sort-order", choices=("asc", "desc"), default="desc")

    parser.add_argument("--cookie", help="Raw Cookie header. Can also be set with TRADINGVIEW_COOKIE.")
    parser.add_argument("--cookie-file", help="Cookie file: Netscape format, JSON export, or raw Cookie header text.")
    parser.add_argument("--check-auth", action="store_true", help="Check whether the supplied cookies are authenticated.")
    parser.add_argument("--dry-run", action="store_true", help="Print the scanner payload and exit.")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr.")
    return parser.parse_args()


def export_csv(args: argparse.Namespace) -> int:
    if args.page_size < 1 or args.page_size > 5000:
        raise ValueError("--page-size must be between 1 and 5000")
    if args.limit < 0:
        raise ValueError("--limit must be 0 or a positive integer")

    specs = parse_columns(args.columns)
    client = TradingViewCexClient(client_config_from_namespace(args))
    if args.check_auth:
        client.check_auth()

    first_payload = client.build_payload(specs, 0, min(args.page_size, args.limit or args.page_size))
    if args.dry_run:
        print(json.dumps(first_payload, ensure_ascii=False, indent=2))
        return 0

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = client.fetch_snapshot(args.limit, specs)

    with output_path.open("w", newline="", encoding=args.encoding) as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=[spec.header for spec in specs], delimiter=args.delimiter)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_row(specs))

    if args.verbose:
        print(f"done: {len(rows)} rows -> {output_path}", file=sys.stderr)
    return len(rows)


def main() -> int:
    args = parse_args()
    try:
        export_csv(args)
        return 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

