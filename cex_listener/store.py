from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .models import CexEvent, CexRow, dt_to_iso, parse_dt, utc_now


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def load_latest_rows(self) -> list[CexRow]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select
                    symbol_id, ticker, description, exchange, type, base_currency,
                    quote_currency, price, change_24h_pct, volume_24h,
                    volume_24h_change_pct, technical_rating_1d, updated_at, raw_json
                from latest_rows
                """
            ).fetchall()
        result: list[CexRow] = []
        for row in rows:
            raw = json.loads(row["raw_json"])
            result.append(
                CexRow(
                    symbol_id=row["symbol_id"],
                    ticker=row["ticker"],
                    description=row["description"],
                    exchange=row["exchange"],
                    type=row["type"],
                    base_currency=row["base_currency"],
                    quote_currency=row["quote_currency"],
                    price=row["price"],
                    change_24h_pct=row["change_24h_pct"],
                    volume_24h=row["volume_24h"],
                    volume_24h_change_pct=row["volume_24h_change_pct"],
                    technical_rating_1d=row["technical_rating_1d"],
                    fetched_at=parse_dt(row["updated_at"]),
                    raw=raw,
                )
            )
        return result

    def save_snapshot(
        self,
        *,
        fetched_at,
        row_count: int,
        duration_ms: int,
        status: str,
        error: str | None = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                insert into snapshots (fetched_at, row_count, duration_ms, status, error)
                values (?, ?, ?, ?, ?)
                """,
                (dt_to_iso(fetched_at), row_count, duration_ms, status, error),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def save_error_snapshot(self, exc: Exception, *, duration_ms: int = 0) -> int:
        return self.save_snapshot(
            fetched_at=utc_now(),
            row_count=0,
            duration_ms=duration_ms,
            status="error",
            error=str(exc),
        )

    def save_latest_rows(self, rows: list[CexRow]) -> None:
        with closing(self._connect()) as conn:
            conn.execute("delete from latest_rows")
            conn.executemany(
                """
                insert into latest_rows (
                    symbol_id, ticker, description, exchange, type, base_currency,
                    quote_currency, price, change_24h_pct, volume_24h,
                    volume_24h_change_pct, technical_rating_1d, updated_at, raw_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.symbol_id,
                        row.ticker,
                        row.description,
                        row.exchange,
                        row.type,
                        row.base_currency,
                        row.quote_currency,
                        row.price,
                        row.change_24h_pct,
                        row.volume_24h,
                        row.volume_24h_change_pct,
                        row.technical_rating_1d,
                        dt_to_iso(row.fetched_at),
                        json.dumps(row.raw, ensure_ascii=False, separators=(",", ":")),
                    )
                    for row in rows
                ],
            )
            conn.commit()

    def save_events(self, events: list[CexEvent], snapshot_id: int | None) -> int:
        if not events:
            return 0
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                insert or ignore into events (
                    event_id, event_type, symbol_id, field, old_value, new_value,
                    detected_at, snapshot_id, raw_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event.event_id,
                        event.event_type,
                        event.symbol_id,
                        event.field,
                        _json_value(event.old_value),
                        _json_value(event.new_value),
                        dt_to_iso(event.detected_at),
                        snapshot_id,
                        event.compact_json(),
                    )
                    for event in events
                ],
            )
            count = int(conn.total_changes)
            conn.commit()
            return count

    def status(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            snapshot = conn.execute(
                "select * from snapshots order by id desc limit 1",
            ).fetchone()
            events_last_hour = conn.execute(
                """
                select count(*) as count
                from events
                where datetime(replace(detected_at, 'Z', '+00:00')) >= datetime('now', '-1 hour')
                """
            ).fetchone()["count"]
            latest_count = conn.execute("select count(*) as count from latest_rows").fetchone()["count"]
        return {
            "db": str(self.path),
            "latest_rows": latest_count,
            "events_last_hour": events_last_hour,
            "last_snapshot": dict(snapshot) if snapshot else None,
        }

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                create table if not exists snapshots (
                    id integer primary key autoincrement,
                    fetched_at text not null,
                    row_count integer not null,
                    duration_ms integer not null,
                    status text not null,
                    error text
                );

                create table if not exists latest_rows (
                    symbol_id text primary key,
                    ticker text,
                    description text,
                    exchange text,
                    type text,
                    base_currency text,
                    quote_currency text,
                    price real,
                    change_24h_pct real,
                    volume_24h real,
                    volume_24h_change_pct real,
                    technical_rating_1d real,
                    updated_at text not null,
                    raw_json text not null
                );

                create table if not exists events (
                    id integer primary key autoincrement,
                    event_id text not null unique,
                    event_type text not null,
                    symbol_id text not null,
                    field text,
                    old_value text,
                    new_value text,
                    detected_at text not null,
                    snapshot_id integer,
                    raw_json text not null
                );

                create index if not exists idx_events_detected_at on events(detected_at);
                create index if not exists idx_events_symbol_id on events(symbol_id);
                create index if not exists idx_latest_exchange on latest_rows(exchange);
                create index if not exists idx_latest_type on latest_rows(type);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _json_value(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
