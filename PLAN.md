# Plan: Continuous TradingView CEX Listener

Документ описывает план добавления listener, который будет постоянно следить за обновлениями CEX-скринера TradingView и фиксировать изменения.

## 1. Цель listener

Текущий `tradingview_cex_exporter.py` делает batch export в CSV. Listener должен работать постоянно:

1. периодически опрашивать scanner API;
2. получать актуальные строки CEX-таблицы;
3. сравнивать новый snapshot с предыдущим состоянием;
4. находить изменения;
5. сохранять snapshot и events;
6. опционально отправлять события наружу.

## 2. Что именно слушать

Минимальный MVP должен отслеживать:


- появление новой пары;
- исчезновение пары из результата;
- изменение цены;
- изменение 24h change;
- изменение 24h volume;
- изменение 24h volume change;
- изменение technical rating;
- изменение типа пары или биржи, если TradingView начнет возвращать другие metadata.

Базовый identity ключ:

```text
symbol_id
```

Пример:

```text
BINANCE:BTCUSDT.P
```

Для diff нельзя использовать только `ticker`, потому что одинаковые тикеры есть на разных биржах.

## 3. Предлагаемая архитектура

```text
TradingView scanner API
        |
        v
TradingViewCexClient
        |
        v
SnapshotFetcher
        |
        v
StateStore <-> DiffEngine
        |
        v
EventSink(s)
```

Компоненты:

- `TradingViewCexClient` - низкоуровневый API-клиент.
- `SnapshotFetcher` - получает текущий snapshot с пагинацией.
- `StateStore` - хранит последнее состояние и историю.
- `DiffEngine` - сравнивает old/new и генерирует события.
- `EventSink` - пишет события в JSONL/CSV/log/webhook/Telegram/etc.
- `ListenerRunner` - основной бесконечный цикл.

## 4. Рекомендуемая структура файлов

```text
cex_listener/
  __init__.py
  client.py
  config.py
  diff.py
  models.py
  runner.py
  store.py
  sinks.py
  utils.py

tradingview_cex_exporter.py
listener.py
listener_config.example.json
PIPELINE.md
PLAN.md
README.md
requirements.txt
```

Для начала можно не выносить все сразу в пакет и сделать один `listener.py`, но лучше сразу отделить client/diff/store, потому что exporter и listener используют одну и ту же API-логику.

## 5. Шаг 1: выделить общий API-клиент

Сейчас логика scanner API живет в `tradingview_cex_exporter.py`.

Нужно вынести переиспользуемые части:

- `DEFAULT_COLUMNS`;
- `FIELD_ALIASES`;
- построение `filter2`;
- cookies/session;
- `build_payload`;
- `post_scan`;
- пагинацию.

Новый модуль:

```text
cex_listener/client.py
```

Ожидаемый интерфейс:

```python
class TradingViewCexClient:
    def __init__(self, config: ClientConfig) -> None:
        ...

    def fetch_page(self, offset: int, page_size: int) -> ScanPage:
        ...

    def fetch_snapshot(self, limit: int | None = None) -> list[CexRow]:
        ...
```

`tradingview_cex_exporter.py` после этого должен использовать этот client, а не дублировать HTTP-код.

## 6. Шаг 2: модели данных

Нужен стабильный внутренний формат строки.

```python
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
```

Событие:

```python
@dataclass(frozen=True)
class CexEvent:
    event_id: str
    event_type: str
    symbol_id: str
    field: str | None
    old_value: Any
    new_value: Any
    detected_at: datetime
```

Типы событий:

- `created`;
- `removed`;
- `changed`;
- `threshold_crossed`;
- `error`;
- `heartbeat`.

## 7. Шаг 3: StateStore

Для MVP лучше использовать SQLite. Он уже есть в Python standard library и не требует отдельного сервера.

Файл:

```text
cex_listener.db
```

Таблицы:

```sql
create table snapshots (
    id integer primary key autoincrement,
    fetched_at text not null,
    row_count integer not null,
    duration_ms integer not null,
    status text not null,
    error text
);
```

```sql
create table latest_rows (
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
```

```sql
create table events (
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
```

Индексы:

```sql
create index idx_events_detected_at on events(detected_at);
create index idx_events_symbol_id on events(symbol_id);
create index idx_latest_exchange on latest_rows(exchange);
create index idx_latest_type on latest_rows(type);
```

## 8. Шаг 4: DiffEngine

Алгоритм:

1. Получить `old_rows` из `latest_rows`.
2. Получить `new_rows` из scanner API.
3. Построить словари:

```python
old_by_symbol = {row.symbol_id: row for row in old_rows}
new_by_symbol = {row.symbol_id: row for row in new_rows}
```

4. Created:

```python
new_keys - old_keys
```

5. Removed:

```python
old_keys - new_keys
```

6. Changed:

```python
old_keys & new_keys
```

7. Для shared keys сравнить watched fields.

Поля для сравнения в MVP:

```python
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
```

Чтобы не генерировать шум, нужны thresholds.

Пример default thresholds:

```json
{
  "price_relative_change_min": 0.001,
  "volume_relative_change_min": 0.05,
  "change_abs_delta_min": 0.25,
  "rating_abs_delta_min": 0.1
}
```

Это значит:

- price event только если цена изменилась минимум на 0.1%;
- volume event только если объем изменился минимум на 5%;
- 24h change event только если абсолютная разница минимум 0.25 percentage points;
- rating event только если абсолютная разница минимум 0.1.

Для первого запуска:

- не генерировать `created` на все 40k строк по умолчанию;
- сохранить baseline;
- записать snapshot event/heartbeat.

Опциональный флаг:

```text
--emit-initial-created
```

если нужно создать events на все первичные строки.

## 9. Шаг 5: Listener loop

Основной loop:

```python
while not stopped:
    started = now()
    try:
        rows = client.fetch_snapshot()
        old_rows = store.load_latest_rows()
        events = diff_engine.diff(old_rows, rows)
        snapshot_id = store.save_snapshot(...)
        store.save_latest_rows(rows)
        store.save_events(events, snapshot_id)
        sinks.emit(events)
    except Exception as exc:
        store.save_error_snapshot(exc)
        sinks.emit_error(exc)
        sleep(backoff.next())
    else:
        sleep(interval)
```

Остановка:

- `Ctrl+C`;
- SIGTERM;
- в Windows достаточно KeyboardInterrupt для MVP.

## 10. Шаг 6: Config

Нужен конфиг, чтобы listener не разрастался CLI-флагами.

Файл:

```text
listener_config.example.json
```

Пример:

```json
{
  "poll_interval_seconds": 30,
  "page_size": 1000,
  "limit": 0,
  "output_db": "cex_listener.db",
  "events_jsonl": "events.jsonl",
  "filters": {
    "exchange": ["BINANCE", "OKX", "BYBIT"],
    "type": ["spot", "swap"],
    "quote": ["USDT"],
    "min_volume": 1000000
  },
  "thresholds": {
    "price_relative_change_min": 0.001,
    "volume_relative_change_min": 0.05,
    "change_abs_delta_min": 0.25,
    "rating_abs_delta_min": 0.1
  },
  "auth": {
    "cookie_file": null,
    "use_env_cookie": true
  },
  "sinks": {
    "stdout": true,
    "jsonl": true,
    "webhook_url": null
  }
}
```

CLI:

```powershell
python .\listener.py --config .\listener_config.json
```

Overrides:

```powershell
python .\listener.py --config .\listener_config.json --interval 10 --verbose
```

## 11. Шаг 7: Event sinks

MVP sinks:

1. stdout log;
2. JSONL file;
3. SQLite `events` table.

JSONL example:

```json
{"event_type":"changed","symbol_id":"BINANCE:BTCUSDT","field":"price","old_value":80841.22,"new_value":80900.01,"detected_at":"2026-05-11T10:20:30Z"}
```

Следующий уровень:

- webhook POST;
- Telegram bot;
- Discord webhook;
- email;
- Prometheus metrics.

Webhook payload:

```json
{
  "source": "tradingview_cex_listener",
  "events": [
    {
      "event_type": "changed",
      "symbol_id": "BINANCE:BTCUSDT",
      "field": "price",
      "old_value": 80841.22,
      "new_value": 80900.01,
      "detected_at": "2026-05-11T10:20:30Z"
    }
  ]
}
```

## 12. Шаг 8: Rate limiting/backoff

Scanner API не стоит дергать слишком часто.

Рекомендации:

- default interval: 30-60 секунд;
- page delay: 0.15-0.3 секунды;
- retries: 3;
- exponential backoff при ошибках;
- max backoff: 5 минут;
- jitter: 10-20%, чтобы избежать ровного ритма.

Если выгружается вся таблица 40k строками и page size 1000, один snapshot - это около 41 request. При interval 30 секунд это уже агрессивно. Для full-table listener лучше interval 2-5 минут или фильтровать universe.

Практичный MVP:

- full table: `poll_interval_seconds = 300`;
- top/filter subset: `poll_interval_seconds = 30`;
- small watchlist via `--ticker`: `poll_interval_seconds = 10-30`.

## 13. Шаг 9: Обработка ошибок

Обязательные случаи:

- network timeout;
- HTTP 403/429;
- HTTP 5xx;
- JSON parse error;
- TradingView error `Unknown field`;
- пустой response;
- изменение схемы колонок;
- cookies expired;
- disk/database lock.

Поведение:

- timeout/5xx: retry, потом error snapshot, backoff;
- 403/429: увеличить backoff, записать error event;
- Unknown field: fail fast, потому что это ошибка схемы;
- cookies expired: warning через `auth_status`;
- DB lock: retry коротким backoff.

## 14. Шаг 10: Команды управления

Предлагаемые команды:

```powershell
python .\listener.py run --config .\listener_config.json
python .\listener.py once --config .\listener_config.json
python .\listener.py status --db .\cex_listener.db
python .\listener.py export-events --db .\cex_listener.db -o events.csv
```

`once` полезен для теста: делает один snapshot, считает diff и выходит.

`status` показывает:

- последний snapshot time;
- row count;
- last error;
- events за последний час;
- auth status;
- текущий config hash.

## 15. Этапы реализации

### Этап 1: рефакторинг exporter

- Вынести общий scanner client.
- Оставить текущий CSV exporter рабочим.
- Добавить тест `--dry-run` после рефакторинга.

Результат: exporter и будущий listener используют один API-код.

### Этап 2: SQLite store

- Создать `store.py`.
- Реализовать migrations/init.
- Реализовать load/save latest rows.
- Реализовать save snapshots/events.

Результат: можно сохранить baseline.

### Этап 3: one-shot diff

- Реализовать `listener.py once`.
- Первый запуск сохраняет baseline.
- Второй запуск генерирует события.
- Вывод событий в stdout и JSONL.

Результат: diff logic проверяется без бесконечного процесса.

### Этап 4: постоянный runner

- Реализовать `listener.py run`.
- Добавить graceful shutdown.
- Добавить backoff.
- Добавить heartbeat snapshots.

Результат: listener работает постоянно.

### Этап 5: thresholds/config

- Ввести `listener_config.json`.
- Добавить thresholds.
- Добавить фильтры из config.
- Добавить CLI overrides.

Результат: listener можно настраивать без правки кода.

### Этап 6: notification sinks

- JSONL sink.
- Webhook sink.
- Telegram/Discord по необходимости.

Результат: события можно отдавать внешним системам.

### Этап 7: packaging/ops

- Добавить `requirements.txt` при необходимости.
- Добавить пример Windows Task Scheduler/service запуска.
- Добавить логи.
- Добавить rotation для JSONL.

Результат: listener можно держать в фоне длительно.

## 16. Минимальный MVP scope

Для первой рабочей версии достаточно:

- `listener.py once`;
- `listener.py run`;
- SQLite store;
- diff по `symbol_id`;
- события `created`, `removed`, `changed`;
- thresholds для price/volume/change;
- stdout + JSONL;
- config JSON;
- cookie auth reuse из exporter.

Не нужно в MVP:

- web UI;
- Telegram;
- async HTTP;
- PostgreSQL;
- полноценный daemon/service installer;
- сложные alert rules.

## 17. Риски

1. TradingView может изменить field names.
   - Митигация: `dry-run`, fail fast на `Unknown field`, PIPELINE.md с текущими mappings.

2. TradingView может ограничить частоту запросов.
   - Митигация: interval, page delay, backoff, фильтрация universe.

3. Большая таблица будет долго сниматься.
   - Митигация: page size 1000, ограничение universe, snapshot duration metrics.

4. Частые price changes создадут слишком много событий.
   - Митигация: thresholds, batching, event coalescing.

5. Cookies устареют.
   - Митигация: `--check-auth`, auth status event, запуск без auth для публичных данных.

6. CSV не подходит для stateful listener.
   - Митигация: SQLite как основной state store, CSV только export/report.

## 18. Рекомендуемый первый implementation task

Начать не с бесконечного цикла, а с one-shot diff:

```powershell
python .\listener.py once --config .\listener_config.json --verbose
```

Почему:

- проще тестировать;
- сразу проверяет client, store и diff;
- не требует управления долгоживущим процессом;
- после этого `run` становится тонкой оберткой вокруг `once + sleep`.

## 19. Acceptance criteria для listener MVP

Listener считается готовым, когда:

1. Первый запуск создает SQLite DB и baseline без массового шума.
2. Второй запуск корректно сравнивает snapshot с baseline.
3. Изменение цены/объема генерирует `changed` event только при превышении threshold.
4. Новая пара генерирует `created`.
5. Исчезнувшая пара генерирует `removed`.
6. Ошибка TradingView API записывается как error snapshot и не ломает следующий цикл.
7. `Ctrl+C` останавливает `run` без повреждения DB.
8. Все параметры CEX-фильтров из exporter доступны listener через config.
9. CSV exporter продолжает работать после рефакторинга.
