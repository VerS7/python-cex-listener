from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def dt_to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


NUMERIC_FIELDS = {
    "price",
    "change_24h_pct",
    "volume_24h",
    "volume_24h_change_pct",
    "technical_rating_1d",
}


@dataclass(frozen=True)
class CexRow:
    symbol_id: str
    ticker: str | None
    description: str | None
    exchange: str | None
    type: str | None
    base_currency: str | None
    quote_currency: str | None
    price: float | None
    change_24h_pct: float | None
    volume_24h: float | None
    volume_24h_change_pct: float | None
    technical_rating_1d: float | None
    fetched_at: datetime
    raw: dict[str, Any]

    @classmethod
    def from_scan_item(
        cls,
        item: dict[str, Any],
        specs: list[ColumnSpec],
        fetched_at: datetime,
    ) -> CexRow:
        values = item.get("d") or []
        api_columns = [spec.api_name for spec in specs if spec.api_name != "__symbol_id__"]
        value_by_api_name = dict(zip(api_columns, values))
        row: dict[str, Any] = {}
        for spec in specs:
            if spec.api_name == "__symbol_id__":
                row[spec.header] = item.get("s")
            else:
                row[spec.header] = value_by_api_name.get(spec.api_name)

        symbol_id = str(row.get("symbol_id") or item.get("s") or "")
        if not symbol_id:
            raise ValueError(f"Scanner item has no symbol id: {item!r}")

        return cls(
            symbol_id=symbol_id,
            ticker=_optional_str(row.get("ticker")),
            description=_optional_str(row.get("description")),
            exchange=_optional_str(row.get("exchange")),
            type=_optional_str(row.get("type")),
            base_currency=_optional_str(row.get("base_currency")),
            quote_currency=_optional_str(row.get("quote_currency")),
            price=_optional_float(row.get("price")),
            change_24h_pct=_optional_float(row.get("change_24h_pct")),
            volume_24h=_optional_float(row.get("volume_24h")),
            volume_24h_change_pct=_optional_float(row.get("volume_24h_change_pct")),
            technical_rating_1d=_optional_float(row.get("technical_rating_1d")),
            fetched_at=fetched_at,
            raw=row,
        )

    def to_storage_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fetched_at"] = dt_to_iso(self.fetched_at)
        return data

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> CexRow:
        return cls(
            symbol_id=str(data["symbol_id"]),
            ticker=_optional_str(data.get("ticker")),
            description=_optional_str(data.get("description")),
            exchange=_optional_str(data.get("exchange")),
            type=_optional_str(data.get("type")),
            base_currency=_optional_str(data.get("base_currency")),
            quote_currency=_optional_str(data.get("quote_currency")),
            price=_optional_float(data.get("price")),
            change_24h_pct=_optional_float(data.get("change_24h_pct")),
            volume_24h=_optional_float(data.get("volume_24h")),
            volume_24h_change_pct=_optional_float(data.get("volume_24h_change_pct")),
            technical_rating_1d=_optional_float(data.get("technical_rating_1d")),
            fetched_at=parse_dt(str(data["fetched_at"])),
            raw=dict(data.get("raw") or {}),
        )

    def to_csv_row(self, specs: list[ColumnSpec]) -> dict[str, Any]:
        values = self.raw.copy()
        values.setdefault("symbol_id", self.symbol_id)
        return {spec.header: values.get(spec.header) for spec in specs}

    def compact_json(self) -> str:
        return json.dumps(self.to_storage_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class ScanPage:
    rows: list[CexRow]
    total_count: int
    offset: int
    page_size: int
    fetched_at: datetime


@dataclass(frozen=True)
class CexEvent:
    event_id: str
    event_type: str
    symbol_id: str
    field: str | None
    old_value: Any
    new_value: Any
    detected_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "symbol_id": self.symbol_id,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "detected_at": dt_to_iso(self.detected_at),
        }

    def compact_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

