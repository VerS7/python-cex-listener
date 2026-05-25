from __future__ import annotations

import csv
import json
import random
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .client import TradingViewCexClient
from .config import ListenerConfig
from .diff import DiffEngine
from .models import CexEvent, dt_to_iso, utc_now
from .sinks import EventSink, build_sink
from .store import StateStore


def run_once(config: ListenerConfig, *, verbose: bool = False) -> list[CexEvent]:
    started = time.monotonic()
    fetched_at = utc_now()
    store = StateStore(config.output_db)
    sink = build_sink(config.sinks, config.events_jsonl, config.sheet_export)
    client = TradingViewCexClient(config.client)
    diff_engine = DiffEngine(config.thresholds)

    try:
        new_rows = client.fetch_snapshot(config.limit)
        old_rows = store.load_latest_rows()
        events = diff_engine.diff(old_rows, new_rows, emit_initial_created=config.emit_initial_created)
        duration_ms = int((time.monotonic() - started) * 1000)
        snapshot_id = store.save_snapshot(
            fetched_at=fetched_at,
            row_count=len(new_rows),
            duration_ms=duration_ms,
            status="ok",
        )
        store.save_latest_rows(new_rows)
        store.save_events(events, snapshot_id)
        sink.emit(events)
        if verbose:
            print(f"snapshot ok: rows={len(new_rows)} events={len(events)} duration_ms={duration_ms}")
        return events
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        store.save_error_snapshot(exc, duration_ms=duration_ms)
        sink.emit_error(exc)
        raise


def run_loop(config: ListenerConfig, *, verbose: bool = False) -> None:
    failures = 0
    while True:
        try:
            run_once(config, verbose=verbose)
            failures = 0
            time.sleep(config.poll_interval_seconds)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            failures += 1
            if verbose:
                print(f"listener error: {exc}")
            time.sleep(_backoff_seconds(failures))


def status(db_path: str) -> dict[str, Any]:
    return StateStore(db_path).status()


def export_events(db_path: str, output_path: str) -> int:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select id, event_id, event_type, symbol_id, field, old_value,
                   new_value, detected_at, snapshot_id
            from events
            order by id
            """
        ).fetchall()
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        fieldnames = [
            "id",
            "event_id",
            "event_type",
            "symbol_id",
            "field",
            "old_value",
            "new_value",
            "detected_at",
            "snapshot_id",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return len(rows)


def with_overrides(
    config: ListenerConfig,
    *,
    interval: float | None = None,
    limit: int | None = None,
    output_db: str | None = None,
    events_jsonl: str | None = None,
    emit_initial_created: bool | None = None,
) -> ListenerConfig:
    client = config.client
    if limit is not None:
        config = replace(config, limit=limit)
    if interval is not None:
        config = replace(config, poll_interval_seconds=interval)
    if output_db is not None:
        config = replace(config, output_db=output_db)
    if events_jsonl is not None:
        config = replace(config, events_jsonl=events_jsonl)
    if emit_initial_created is not None:
        config = replace(config, emit_initial_created=emit_initial_created)
    return replace(config, client=client)


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default))


def _backoff_seconds(failures: int) -> float:
    base = min(300.0, 2.0**min(failures, 8))
    jitter = random.uniform(0.9, 1.2)
    return base * jitter


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return dt_to_iso(value)
    return str(value)

