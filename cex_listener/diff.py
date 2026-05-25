from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .models import CexEvent, CexRow, dt_to_iso, utc_now


WATCHED_FIELDS = [
    "price",
    "change_24h_pct",
    "volume_24h",
    "volume_24h_change_pct",
    "technical_rating_1d",
    "type",
    "base_currency",
    "quote_currency",
]


@dataclass(frozen=True)
class Thresholds:
    price_relative_change_min: float = 0.001
    volume_relative_change_min: float = 0.05
    change_abs_delta_min: float = 0.25
    rating_abs_delta_min: float = 0.1

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Thresholds:
        data = data or {}
        return cls(
            price_relative_change_min=float(data.get("price_relative_change_min", cls.price_relative_change_min)),
            volume_relative_change_min=float(data.get("volume_relative_change_min", cls.volume_relative_change_min)),
            change_abs_delta_min=float(data.get("change_abs_delta_min", cls.change_abs_delta_min)),
            rating_abs_delta_min=float(data.get("rating_abs_delta_min", cls.rating_abs_delta_min)),
        )


class DiffEngine:
    def __init__(self, thresholds: Thresholds | None = None, watched_fields: list[str] | None = None) -> None:
        self.thresholds = thresholds or Thresholds()
        self.watched_fields = watched_fields or list(WATCHED_FIELDS)

    def diff(
        self,
        old_rows: list[CexRow],
        new_rows: list[CexRow],
        *,
        emit_initial_created: bool = False,
    ) -> list[CexEvent]:
        old_by_symbol = {row.symbol_id: row for row in old_rows}
        new_by_symbol = {row.symbol_id: row for row in new_rows}
        detected_at = utc_now()
        events: list[CexEvent] = []

        if not old_by_symbol and not emit_initial_created:
            return events

        for symbol_id in sorted(new_by_symbol.keys() - old_by_symbol.keys()):
            new_row = new_by_symbol[symbol_id]
            events.append(
                self._event(
                    "created",
                    symbol_id,
                    None,
                    None,
                    new_row.to_storage_dict(),
                    detected_at,
                )
            )

        for symbol_id in sorted(old_by_symbol.keys() - new_by_symbol.keys()):
            old_row = old_by_symbol[symbol_id]
            events.append(
                self._event(
                    "removed",
                    symbol_id,
                    None,
                    old_row.to_storage_dict(),
                    None,
                    detected_at,
                )
            )

        for symbol_id in sorted(old_by_symbol.keys() & new_by_symbol.keys()):
            old_row = old_by_symbol[symbol_id]
            new_row = new_by_symbol[symbol_id]
            for field in self.watched_fields:
                old_value = getattr(old_row, field)
                new_value = getattr(new_row, field)
                if self._is_meaningful_change(field, old_value, new_value):
                    events.append(self._event("changed", symbol_id, field, old_value, new_value, detected_at))

        return events

    def _is_meaningful_change(self, field: str, old_value: Any, new_value: Any) -> bool:
        if old_value == new_value:
            return False
        if old_value is None or new_value is None:
            return old_value != new_value
        if field == "price":
            return _relative_delta(old_value, new_value) >= self.thresholds.price_relative_change_min
        if field == "volume_24h":
            return _relative_delta(old_value, new_value) >= self.thresholds.volume_relative_change_min
        if field in {"change_24h_pct", "volume_24h_change_pct"}:
            return abs(float(new_value) - float(old_value)) >= self.thresholds.change_abs_delta_min
        if field == "technical_rating_1d":
            return abs(float(new_value) - float(old_value)) >= self.thresholds.rating_abs_delta_min
        return old_value != new_value

    def _event(
        self,
        event_type: str,
        symbol_id: str,
        field: str | None,
        old_value: Any,
        new_value: Any,
        detected_at,
    ) -> CexEvent:
        payload = {
            "event_type": event_type,
            "symbol_id": symbol_id,
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "detected_at": dt_to_iso(detected_at),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        event_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return CexEvent(
            event_id=event_id,
            event_type=event_type,
            symbol_id=symbol_id,
            field=field,
            old_value=old_value,
            new_value=new_value,
            detected_at=detected_at,
        )


def _relative_delta(old_value: Any, new_value: Any) -> float:
    old_float = float(old_value)
    new_float = float(new_value)
    base = abs(old_float)
    if base == 0:
        return float("inf") if new_float != 0 else 0.0
    return abs(new_float - old_float) / base

