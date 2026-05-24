#!/usr/bin/env python3
"""
Export TradingView CEX Screener rows to CSV.

The public CEX screener is backed by TradingView's scanner endpoint. This
script uses the same permanent CEX filter as the page:

    centralization == cex

Authentication, when needed for account-specific access, is handled through
browser cookies rather than username/password because TradingView login can
require 2FA, captcha, or other browser-only checks.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests


SCANNER_URL = "https://scanner.tradingview.com/crypto/scan"
REFERER_URL = "https://ru.tradingview.com/cex-screener/"


@dataclass(frozen=True)
class ColumnSpec:
    header: str
    api_name: str


DEFAULT_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("symbol_id", "__symbol_id__"),
    ColumnSpec("ticker", "name"),
    ColumnSpec("description", "description"),
    ColumnSpec("exchange", "exchange"),
    ColumnSpec("type", "type"),
    ColumnSpec("base_currency", "base_currency"),
    ColumnSpec("quote_currency", "currency"),
    ColumnSpec("price", "close"),
    ColumnSpec("change_24h_pct", "change"),
    ColumnSpec("volume_24h", "24h_vol|5"),
    ColumnSpec("volume_24h_change_pct", "24h_vol_change|5"),
    ColumnSpec("technical_rating_1d", "Recommend.All"),
)


FIELD_ALIASES = {
    "ticker": "name",
    "symbol": "name",
    "quote_currency": "currency",
    "price": "close",
    "change_24h_pct": "change",
    "volume_24h": "24h_vol|5",
    "volume_24h_change_pct": "24h_vol_change|5",
    "technical_rating_1d": "Recommend.All",
}


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


def split_values(values: Iterable[str], *, upper: bool = True) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item:
                result.append(item.upper() if upper else item)
    return result


def normalize_type(value: str) -> str:
    value = value.strip().lower()
    if value == "perpetual":
        return "swap"
    return value


def expression(left: str, operation: str, right: Any) -> dict[str, Any]:
    return {"expression": {"left": left, "operation": operation, "right": right}}


def add_any_of_filter(operands: list[dict[str, Any]], field: str, values: list[str]) -> None:
    if not values:
        return
    if len(values) == 1:
        operands.append(expression(field, "equal", values[0]))
        return
    operands.append(
        {
            "operator": "or",
            "operands": [expression(field, "equal", value) for value in values],
        }
    )


def add_numeric_range(
    operands: list[dict[str, Any]],
    field: str,
    min_value: float | None,
    max_value: float | None,
) -> None:
    if min_value is not None and max_value is not None:
        operands.append(expression(field, "in_range", [min_value, max_value]))
    elif min_value is not None:
        operands.append(expression(field, "egreater", min_value))
    elif max_value is not None:
        operands.append(expression(field, "eless", max_value))


def load_json_or_file(value: str) -> Any:
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


def append_extra_filter(operands: list[dict[str, Any]], raw_filter: str | None) -> None:
    if not raw_filter:
        return
    extra = load_json_or_file(raw_filter)
    if isinstance(extra, list):
        operands.extend(extra)
    elif isinstance(extra, dict) and "expression" in extra:
        operands.append(extra)
    elif isinstance(extra, dict) and {"operator", "operands"} <= set(extra):
        operands.append(extra)
    else:
        raise ValueError("--filter-json must be an expression, operands list, or filter2 object")


def build_filter2(args: argparse.Namespace) -> dict[str, Any]:
    operands: list[dict[str, Any]] = [
        expression("centralization", "equal", "cex"),
    ]

    add_any_of_filter(operands, "exchange", split_values(args.exchange))
    add_any_of_filter(operands, "type", [normalize_type(v) for v in split_values(args.type, upper=False)])
    add_any_of_filter(operands, "base_currency", split_values(args.base))
    add_any_of_filter(operands, "currency", split_values(args.quote))

    add_numeric_range(operands, "close", args.min_price, args.max_price)
    add_numeric_range(operands, "change", args.min_change, args.max_change)
    add_numeric_range(operands, "24h_vol|5", args.min_volume, args.max_volume)
    add_numeric_range(operands, "24h_vol_change|5", args.min_volume_change, args.max_volume_change)
    append_extra_filter(operands, args.filter_json)
    return {"operator": "and", "operands": operands}


def parse_columns(raw_columns: str | None) -> list[ColumnSpec]:
    if not raw_columns:
        return list(DEFAULT_COLUMNS)
    specs: list[ColumnSpec] = [ColumnSpec("symbol_id", "__symbol_id__")]
    for raw in raw_columns.split(","):
        token = raw.strip()
        if not token:
            continue
        api_name = FIELD_ALIASES.get(token, token)
        specs.append(ColumnSpec(token, api_name))
    return specs


def read_cookie_file(path: str) -> tuple[dict[str, str], str | None]:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return {}, None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    cookies: dict[str, str] = {}
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if isinstance(value, (str, int, float)):
                cookies[str(key)] = str(value)
        return cookies, None

    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and "name" in item and "value" in item:
                cookies[str(item["name"])] = str(item["value"])
        return cookies, None

    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    netscape_like = all(len(line.split("\t")) >= 7 for line in lines)
    if netscape_like:
        for line in lines:
            parts = line.split("\t")
            cookies[parts[5]] = parts[6]
        return cookies, None

    return {}, text


def apply_auth(session: requests.Session, args: argparse.Namespace) -> None:
    raw_cookie = args.cookie or os.environ.get("TRADINGVIEW_COOKIE")
    if args.cookie_file:
        cookies, cookie_header = read_cookie_file(args.cookie_file)
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=".tradingview.com")
        raw_cookie = raw_cookie or cookie_header
    if raw_cookie:
        session.headers["Cookie"] = raw_cookie


def make_session(args: argparse.Namespace) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://ru.tradingview.com",
            "Referer": REFERER_URL,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        }
    )
    apply_auth(session, args)
    return session


def check_auth(session: requests.Session, timeout: float) -> None:
    response = session.get(REFERER_URL, timeout=timeout)
    response.raise_for_status()
    html = response.text
    if "is-authenticated" in html and "is-not-authenticated" not in html:
        print("TradingView auth: authenticated", file=sys.stderr)
    else:
        print("TradingView auth: not authenticated or cookies were not accepted", file=sys.stderr)


def build_payload(
    args: argparse.Namespace,
    specs: list[ColumnSpec],
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    columns = [spec.api_name for spec in specs if spec.api_name != "__symbol_id__"]
    payload: dict[str, Any] = {
        "columns": columns,
        "filter2": build_filter2(args),
        "markets": ["crypto"],
        "options": {"lang": args.lang},
        "range": [offset, offset + page_size],
        "sort": {"sortBy": FIELD_ALIASES.get(args.sort_by, args.sort_by), "sortOrder": args.sort_order},
    }

    tickers = split_values(args.ticker, upper=True)
    if tickers:
        payload["symbols"] = {"tickers": tickers, "query": {"types": []}}
    return payload


def post_scan(session: requests.Session, payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, args.retries + 1):
        try:
            response = session.post(SCANNER_URL, json=payload, timeout=args.timeout)
            if response.status_code >= 500:
                raise requests.HTTPError(f"{response.status_code}: {response.text[:300]}", response=response)
            response.raise_for_status()
            data = response.json()
            if data.get("data") is None and data.get("error"):
                raise RuntimeError(data["error"])
            return data
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt == args.retries:
                break
            time.sleep(min(2.0 * attempt, 8.0))
    raise RuntimeError(f"TradingView scanner request failed: {last_error}") from last_error


def row_from_item(item: dict[str, Any], specs: list[ColumnSpec]) -> dict[str, Any]:
    values = item.get("d") or []
    api_columns = [spec.api_name for spec in specs if spec.api_name != "__symbol_id__"]
    value_by_api_name = dict(zip(api_columns, values))
    row: dict[str, Any] = {}
    for spec in specs:
        if spec.api_name == "__symbol_id__":
            row[spec.header] = item.get("s")
        else:
            row[spec.header] = value_by_api_name.get(spec.api_name)
    return row


def export_csv(args: argparse.Namespace) -> int:
    if args.page_size < 1 or args.page_size > 5000:
        raise ValueError("--page-size must be between 1 and 5000")
    if args.limit < 0:
        raise ValueError("--limit must be 0 or a positive integer")

    specs = parse_columns(args.columns)
    session = make_session(args)
    if args.check_auth:
        check_auth(session, args.timeout)

    first_payload = build_payload(args, specs, 0, min(args.page_size, args.limit or args.page_size))
    if args.dry_run:
        print(json.dumps(first_payload, ensure_ascii=False, indent=2))
        return 0

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    exported = 0
    total_count: int | None = None
    with output_path.open("w", newline="", encoding=args.encoding) as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=[spec.header for spec in specs], delimiter=args.delimiter)
        writer.writeheader()

        while True:
            remaining = args.limit - exported if args.limit else args.page_size
            if args.limit and remaining <= 0:
                break
            page_size = min(args.page_size, remaining) if args.limit else args.page_size
            payload = build_payload(args, specs, exported, page_size)
            data = post_scan(session, payload, args)
            total_count = int(data.get("totalCount") or 0)
            rows = data.get("data") or []
            if not rows:
                break

            for item in rows:
                writer.writerow(row_from_item(item, specs))
            exported += len(rows)

            if args.verbose:
                target = min(total_count, args.limit) if args.limit else total_count
                print(f"exported {exported}/{target}", file=sys.stderr)

            if exported >= total_count:
                break
            if args.delay:
                time.sleep(args.delay)

    if args.verbose:
        print(f"done: {exported} rows -> {output_path}", file=sys.stderr)
    return exported


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
