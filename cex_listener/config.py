from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import ClientConfig, ScannerFilters
from .diff import Thresholds


@dataclass(frozen=True)
class SinkConfig:
    stdout: bool = True
    jsonl: bool = True
    webhook_url: str | None = None


@dataclass(frozen=True)
class SheetExportConfig:
    enabled: bool = False
    path: str = "signals_for_sheet.csv"
    template: str = "Лонг1"
    event_templates: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ListenerConfig:
    poll_interval_seconds: float = 300.0
    page_size: int = 1000
    page_delay_seconds: float = 0.15
    limit: int = 0
    output_db: str = "cex_listener.db"
    events_jsonl: str = "events.jsonl"
    emit_initial_created: bool = False
    client: ClientConfig = field(default_factory=ClientConfig)
    thresholds: Thresholds = field(default_factory=Thresholds)
    sinks: SinkConfig = field(default_factory=SinkConfig)
    sheet_export: SheetExportConfig = field(default_factory=SheetExportConfig)

    @classmethod
    def load(cls, path: str | Path) -> ListenerConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ListenerConfig:
        filters_data = data.get("filters") or {}
        auth_data = data.get("auth") or {}
        sinks_data = data.get("sinks") or {}
        sheet_data = data.get("sheet_export") or {}

        filters = ScannerFilters(
            exchange=_str_list(filters_data.get("exchange")),
            type=[_normalize_type(value) for value in _str_list(filters_data.get("type"))],
            base=_str_list(filters_data.get("base")),
            quote=_str_list(filters_data.get("quote")),
            ticker=_str_list(filters_data.get("ticker")),
            min_price=_optional_float(filters_data.get("min_price")),
            max_price=_optional_float(filters_data.get("max_price")),
            min_change=_optional_float(filters_data.get("min_change")),
            max_change=_optional_float(filters_data.get("max_change")),
            min_volume=_optional_float(filters_data.get("min_volume")),
            max_volume=_optional_float(filters_data.get("max_volume")),
            min_volume_change=_optional_float(filters_data.get("min_volume_change")),
            max_volume_change=_optional_float(filters_data.get("max_volume_change")),
            filter_json=filters_data.get("filter_json"),
        )

        client = ClientConfig(
            page_size=int(data.get("page_size", 1000)),
            delay=float(data.get("page_delay_seconds", data.get("delay", 0.15))),
            timeout=float(data.get("timeout", 30.0)),
            retries=int(data.get("retries", 3)),
            lang=str(data.get("lang", "ru")),
            sort_by=str(data.get("sort_by", "24h_vol|5")),
            sort_order=str(data.get("sort_order", "desc")),
            cookie=auth_data.get("cookie"),
            cookie_file=auth_data.get("cookie_file"),
            filters=filters,
        )

        return cls(
            poll_interval_seconds=float(data.get("poll_interval_seconds", 300.0)),
            page_size=client.page_size,
            page_delay_seconds=client.delay,
            limit=int(data.get("limit", 0)),
            output_db=str(data.get("output_db", "cex_listener.db")),
            events_jsonl=str(data.get("events_jsonl", "events.jsonl")),
            emit_initial_created=bool(data.get("emit_initial_created", False)),
            client=client,
            thresholds=Thresholds.from_dict(data.get("thresholds")),
            sinks=SinkConfig(
                stdout=bool(sinks_data.get("stdout", True)),
                jsonl=bool(sinks_data.get("jsonl", True)),
                webhook_url=sinks_data.get("webhook_url"),
            ),
            sheet_export=SheetExportConfig(
                enabled=bool(sheet_data.get("enabled", False)),
                path=str(sheet_data.get("path", "signals_for_sheet.csv")),
                template=str(sheet_data.get("template", "Лонг1")),
                event_templates=dict(sheet_data.get("event_templates") or {}),
            ),
        )


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = value.split(",")
    else:
        raw_values = list(value)
    return [str(item).strip().upper() for item in raw_values if str(item).strip()]


def _normalize_type(value: str) -> str:
    value = value.strip().lower()
    if value == "perpetual":
        return "swap"
    return value


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

