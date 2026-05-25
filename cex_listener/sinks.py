from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import requests

from .config import SheetExportConfig, SinkConfig
from .models import CexEvent


class EventSink:
    def emit(self, events: list[CexEvent]) -> None:
        raise NotImplementedError

    def emit_error(self, exc: Exception) -> None:
        raise NotImplementedError


class StdoutSink(EventSink):
    def emit(self, events: list[CexEvent]) -> None:
        for event in events:
            print(event.compact_json())

    def emit_error(self, exc: Exception) -> None:
        print(json.dumps({"event_type": "error", "error": str(exc)}, ensure_ascii=False))


class JsonlSink(EventSink):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, events: list[CexEvent]) -> None:
        if not events:
            return
        with self.path.open("a", encoding="utf-8") as file:
            for event in events:
                file.write(event.compact_json() + "\n")

    def emit_error(self, exc: Exception) -> None:
        payload = {"event_type": "error", "error": str(exc)}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


class WebhookSink(EventSink):
    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout

    def emit(self, events: list[CexEvent]) -> None:
        if not events:
            return
        payload = {
            "source": "tradingview_cex_listener",
            "events": [event.to_dict() for event in events],
        }
        response = requests.post(self.url, json=payload, timeout=self.timeout)
        response.raise_for_status()

    def emit_error(self, exc: Exception) -> None:
        payload = {"source": "tradingview_cex_listener", "events": [{"event_type": "error", "error": str(exc)}]}
        response = requests.post(self.url, json=payload, timeout=self.timeout)
        response.raise_for_status()


class GoogleSheetCsvSink(EventSink):
    """Append events in the sample Google Sheet shape: Тикер,Время,Дата,Шаблон."""

    headers = ["Тикер", "Время", "Дата", "Шаблон"]

    def __init__(self, config: SheetExportConfig) -> None:
        self.config = config
        self.path = Path(config.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=self.headers)
                writer.writeheader()

    def emit(self, events: list[CexEvent]) -> None:
        if not events:
            return
        with self.path.open("a", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=self.headers)
            for event in events:
                writer.writerow(self._row(event))

    def emit_error(self, exc: Exception) -> None:
        return

    def _row(self, event: CexEvent) -> dict[str, str]:
        detected_at = event.detected_at.astimezone()
        return {
            "Тикер": _ticker_for_event(event),
            "Время": f"{detected_at.hour}:{detected_at.minute:02d}",
            "Дата": detected_at.strftime("%d.%m.%Y"),
            "Шаблон": self._template_for_event(event),
        }

    def _template_for_event(self, event: CexEvent) -> str:
        keys = []
        direction = _direction(event.old_value, event.new_value)
        if event.event_type == "changed" and event.field and direction:
            keys.append(f"changed.{event.field}.{direction}")
        if event.event_type == "changed" and event.field:
            keys.append(f"changed.{event.field}")
        keys.append(event.event_type)
        for key in keys:
            if key in self.config.event_templates:
                return self.config.event_templates[key]
        return self.config.template


class CompositeSink(EventSink):
    def __init__(self, sinks: list[EventSink]) -> None:
        self.sinks = sinks

    def emit(self, events: list[CexEvent]) -> None:
        for sink in self.sinks:
            sink.emit(events)

    def emit_error(self, exc: Exception) -> None:
        for sink in self.sinks:
            sink.emit_error(exc)


def build_sink(config: SinkConfig, jsonl_path: str, sheet_config: SheetExportConfig) -> EventSink:
    sinks: list[EventSink] = []
    if config.stdout:
        sinks.append(StdoutSink())
    if config.jsonl:
        sinks.append(JsonlSink(jsonl_path))
    if sheet_config.enabled:
        sinks.append(GoogleSheetCsvSink(sheet_config))
    if config.webhook_url:
        sinks.append(WebhookSink(config.webhook_url))
    return CompositeSink(sinks)


def _ticker_for_event(event: CexEvent) -> str:
    for value in (event.new_value, event.old_value):
        if isinstance(value, dict) and value.get("ticker"):
            return str(value["ticker"])
    if ":" in event.symbol_id:
        return event.symbol_id.split(":", 1)[1]
    return event.symbol_id


def _direction(old_value: Any, new_value: Any) -> str | None:
    try:
        old_float = float(old_value)
        new_float = float(new_value)
    except (TypeError, ValueError):
        return None
    if new_float > old_float:
        return "up"
    if new_float < old_float:
        return "down"
    return None

