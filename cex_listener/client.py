from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests

from .models import CexRow, ColumnSpec, DEFAULT_COLUMNS, FIELD_ALIASES, ScanPage, utc_now


SCANNER_URL = "https://scanner.tradingview.com/crypto/scan"
REFERER_URL = "https://ru.tradingview.com/cex-screener/"


@dataclass(frozen=True)
class ScannerFilters:
    exchange: list[str] = field(default_factory=list)
    type: list[str] = field(default_factory=list)
    base: list[str] = field(default_factory=list)
    quote: list[str] = field(default_factory=list)
    ticker: list[str] = field(default_factory=list)
    min_price: float | None = None
    max_price: float | None = None
    min_change: float | None = None
    max_change: float | None = None
    min_volume: float | None = None
    max_volume: float | None = None
    min_volume_change: float | None = None
    max_volume_change: float | None = None
    filter_json: str | None = None


@dataclass(frozen=True)
class ClientConfig:
    page_size: int = 1000
    delay: float = 0.15
    timeout: float = 30.0
    retries: int = 3
    lang: str = "ru"
    sort_by: str = "24h_vol|5"
    sort_order: str = "desc"
    cookie: str | None = None
    cookie_file: str | None = None
    filters: ScannerFilters = field(default_factory=ScannerFilters)


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


def filters_from_namespace(args: argparse.Namespace) -> ScannerFilters:
    return ScannerFilters(
        exchange=split_values(getattr(args, "exchange", [])),
        type=[normalize_type(v) for v in split_values(getattr(args, "type", []), upper=False)],
        base=split_values(getattr(args, "base", [])),
        quote=split_values(getattr(args, "quote", [])),
        ticker=split_values(getattr(args, "ticker", []), upper=True),
        min_price=getattr(args, "min_price", None),
        max_price=getattr(args, "max_price", None),
        min_change=getattr(args, "min_change", None),
        max_change=getattr(args, "max_change", None),
        min_volume=getattr(args, "min_volume", None),
        max_volume=getattr(args, "max_volume", None),
        min_volume_change=getattr(args, "min_volume_change", None),
        max_volume_change=getattr(args, "max_volume_change", None),
        filter_json=getattr(args, "filter_json", None),
    )


def client_config_from_namespace(args: argparse.Namespace) -> ClientConfig:
    return ClientConfig(
        page_size=getattr(args, "page_size", 1000),
        delay=getattr(args, "delay", 0.15),
        timeout=getattr(args, "timeout", 30.0),
        retries=getattr(args, "retries", 3),
        lang=getattr(args, "lang", "ru"),
        sort_by=getattr(args, "sort_by", "24h_vol|5"),
        sort_order=getattr(args, "sort_order", "desc"),
        cookie=getattr(args, "cookie", None),
        cookie_file=getattr(args, "cookie_file", None),
        filters=filters_from_namespace(args),
    )


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


def build_filter2(filters: ScannerFilters) -> dict[str, Any]:
    operands: list[dict[str, Any]] = [expression("centralization", "equal", "cex")]
    add_any_of_filter(operands, "exchange", filters.exchange)
    add_any_of_filter(operands, "type", filters.type)
    add_any_of_filter(operands, "base_currency", filters.base)
    add_any_of_filter(operands, "currency", filters.quote)
    add_numeric_range(operands, "close", filters.min_price, filters.max_price)
    add_numeric_range(operands, "change", filters.min_change, filters.max_change)
    add_numeric_range(operands, "24h_vol|5", filters.min_volume, filters.max_volume)
    add_numeric_range(operands, "24h_vol_change|5", filters.min_volume_change, filters.max_volume_change)
    append_extra_filter(operands, filters.filter_json)
    return {"operator": "and", "operands": operands}


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


class TradingViewCexClient:
    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.session = self._make_session()

    def check_auth(self) -> None:
        response = self.session.get(REFERER_URL, timeout=self.config.timeout)
        response.raise_for_status()
        html = response.text
        if "is-authenticated" in html and "is-not-authenticated" not in html:
            print("TradingView auth: authenticated", file=sys.stderr)
        else:
            print("TradingView auth: not authenticated or cookies were not accepted", file=sys.stderr)

    def build_payload(self, specs: list[ColumnSpec], offset: int, page_size: int) -> dict[str, Any]:
        columns = [spec.api_name for spec in specs if spec.api_name != "__symbol_id__"]
        payload: dict[str, Any] = {
            "columns": columns,
            "filter2": build_filter2(self.config.filters),
            "markets": ["crypto"],
            "options": {"lang": self.config.lang},
            "range": [offset, offset + page_size],
            "sort": {
                "sortBy": FIELD_ALIASES.get(self.config.sort_by, self.config.sort_by),
                "sortOrder": self.config.sort_order,
            },
        }
        if self.config.filters.ticker:
            payload["symbols"] = {"tickers": self.config.filters.ticker, "query": {"types": []}}
        return payload

    def fetch_page(self, offset: int, page_size: int, specs: list[ColumnSpec] | None = None) -> ScanPage:
        if page_size < 1 or page_size > 5000:
            raise ValueError("page_size must be between 1 and 5000")
        specs = specs or list(DEFAULT_COLUMNS)
        fetched_at = utc_now()
        payload = self.build_payload(specs, offset, page_size)
        data = self._post_scan(payload)
        rows = [CexRow.from_scan_item(item, specs, fetched_at) for item in data.get("data") or []]
        return ScanPage(
            rows=rows,
            total_count=int(data.get("totalCount") or 0),
            offset=offset,
            page_size=page_size,
            fetched_at=fetched_at,
        )

    def fetch_snapshot(self, limit: int | None = None, specs: list[ColumnSpec] | None = None) -> list[CexRow]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be None, 0, or a positive integer")
        max_rows = None if limit in (None, 0) else limit
        specs = specs or list(DEFAULT_COLUMNS)
        rows: list[CexRow] = []
        offset = 0
        while True:
            remaining = max_rows - len(rows) if max_rows is not None else self.config.page_size
            if max_rows is not None and remaining <= 0:
                break
            page_size = min(self.config.page_size, remaining) if max_rows is not None else self.config.page_size
            page = self.fetch_page(offset, page_size, specs)
            if not page.rows:
                break
            rows.extend(page.rows)
            offset += len(page.rows)
            if max_rows is not None and len(rows) >= max_rows:
                break
            if offset >= page.total_count:
                break
            if self.config.delay:
                time.sleep(self.config.delay)
        return rows

    def _make_session(self) -> requests.Session:
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
        self._apply_auth(session)
        return session

    def _apply_auth(self, session: requests.Session) -> None:
        raw_cookie = self.config.cookie or os.environ.get("TRADINGVIEW_COOKIE")
        if self.config.cookie_file:
            cookies, cookie_header = read_cookie_file(self.config.cookie_file)
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=".tradingview.com")
            raw_cookie = raw_cookie or cookie_header
        if raw_cookie:
            session.headers["Cookie"] = raw_cookie

    def _post_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.config.retries + 1):
            try:
                response = self.session.post(SCANNER_URL, json=payload, timeout=self.config.timeout)
                if response.status_code >= 500:
                    raise requests.HTTPError(f"{response.status_code}: {response.text[:300]}", response=response)
                response.raise_for_status()
                data = response.json()
                if data.get("data") is None and data.get("error"):
                    raise RuntimeError(data["error"])
                return data
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt == self.config.retries:
                    break
                time.sleep(min(2.0 * attempt, 8.0))
        raise RuntimeError(f"TradingView scanner request failed: {last_error}") from last_error

