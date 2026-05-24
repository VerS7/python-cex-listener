# Pipeline: TradingView CEX Screener Export

Документ описывает весь текущий пайплайн: как устроена страница TradingView CEX Screener, какой API фактически используется, какие фильтры и колонки были найдены, и как работает локальный Python-экспортер.

## 1. Цель

Нужно выгружать главную таблицу страницы:

`https://ru.tradingview.com/cex-screener/`

в CSV-файл с поддержкой:

- CEX-фильтра, как на сайте;
- пагинации по всей таблице;
- пользовательских фильтров;
- авторизации через TradingView cookies;
- выбора колонок;
- повторных попыток и базовой устойчивости к сетевым ошибкам.

## 2. Что было выяснено про сайт

Страница `ru.tradingview.com/cex-screener/` не хранит таблицу в HTML. HTML отдает оболочку приложения, а данные таблицы загружаются отдельно через scanner API TradingView.

При анализе страницы были проверены frontend-бандлы TradingView:

- основной standalone screener bundle;
- runtime manifest;
- dynamic chunks для CEX presets;
- конфигурация `StandaloneType.CryptoCex`.

В конфигурации TradingView найдено, что CEX-страница является отдельным типом скринера:

`StandaloneType.CryptoCex`

Путь страницы в frontend-конфигурации:

```text
crypto-cex -> cex-screener
```

Прямых endpoints вида `cex/scan` или `cex_pairs/scan` нет. Они возвращали `404`. Рабочий endpoint тот же, что у crypto screener:

```text
POST https://scanner.tradingview.com/crypto/scan
```

Разделение CEX/DEX делается не endpoint-ом, а постоянным фильтром.

## 3. Постоянный фильтр CEX

В frontend-конфигурации CEX-скринера найден permanent filter:

```json
{
  "filter2": {
    "operator": "and",
    "operands": [
      {
        "expression": {
          "left": "centralization",
          "operation": "equal",
          "right": "cex"
        }
      }
    ]
  }
}
```

Именно этот фильтр встроен в экспортер. Без него общий crypto endpoint возвращает также DEX-пулы и другие crypto-инструменты.

## 4. Базовый scanner payload

Минимальный рабочий payload выглядит так:

```json
{
  "columns": [
    "name",
    "description",
    "exchange",
    "type",
    "base_currency",
    "currency",
    "close",
    "change",
    "24h_vol|5",
    "24h_vol_change|5",
    "Recommend.All"
  ],
  "filter2": {
    "operator": "and",
    "operands": [
      {
        "expression": {
          "left": "centralization",
          "operation": "equal",
          "right": "cex"
        }
      }
    ]
  },
  "markets": ["crypto"],
  "options": {
    "lang": "ru"
  },
  "range": [0, 1000],
  "sort": {
    "sortBy": "24h_vol|5",
    "sortOrder": "desc"
  }
}
```

Ответ scanner API имеет форму:

```json
{
  "totalCount": 40574,
  "data": [
    {
      "s": "BINANCE:BTCUSDT.P",
      "d": ["BTCUSDT.P", "..."]
    }
  ]
}
```

`totalCount` меняется со временем. При проверке CEX-фильтр возвращал примерно 40k строк. Значение не должно считаться постоянным.

## 5. Проверенные endpoints

Были проверены следующие варианты:

```text
https://scanner.tradingview.com/crypto/scan
https://scanner.tradingview.com/coin/scan
https://scanner.tradingview.com/cex/scan
https://scanner.tradingview.com/cex_pairs/scan
```

Результат:

- `crypto/scan` работает для CEX-таблицы при наличии `centralization == cex`;
- `coin/scan` возвращает другой universe, не основную CEX-таблицу;
- `cex/scan` и `cex_pairs/scan` не существуют;
- старые поля вроде `Value.Traded|24H` и `24h_vol_cmc` для CEX-таблицы не дают нужную основную колонку объема.

## 6. Найденные API-колонки основной таблицы

Основная overview-таблица CEX в frontend-конфигурации содержит колонки:

- ticker;
- exchange;
- price;
- 24h change;
- 24h volume;
- 24h volume change;
- technical rating.

Для scanner API они мапятся так:

| CSV header | TradingView API field | Комментарий |
|---|---|---|
| `symbol_id` | `s` из ответа | Полный TradingView symbol id, например `BINANCE:BTCUSDT.P` |
| `ticker` | `name` | Тикер без exchange prefix |
| `description` | `description` | Описание пары |
| `exchange` | `exchange` | Биржа |
| `type` | `type` | `spot`, `swap`, иногда другие типы |
| `base_currency` | `base_currency` | Базовая валюта |
| `quote_currency` | `currency` | Котируемая валюта |
| `price` | `close` | Последняя цена |
| `change_24h_pct` | `change` | Изменение за 24h в процентах |
| `volume_24h` | `24h_vol|5` | 24h volume для CEX |
| `volume_24h_change_pct` | `24h_vol_change|5` | Изменение 24h volume |
| `technical_rating_1d` | `Recommend.All` | Technical rating |

Важная деталь: для CEX-таблицы 24h volume - это `24h_vol|5`, а не `Value.Traded|24H` и не `24h_vol_cmc`.

## 7. Фильтры scanner API

Фильтры строятся через `filter2`.

### Равенство

```json
{
  "expression": {
    "left": "exchange",
    "operation": "equal",
    "right": "BINANCE"
  }
}
```

Так работают:

- `exchange`;
- `type`;
- `base_currency`;
- `currency`;
- `centralization`.

### OR по нескольким значениям

Если пользователь передает несколько exchange/type/base/quote, скрипт строит вложенную группу:

```json
{
  "operator": "or",
  "operands": [
    {
      "expression": {
        "left": "exchange",
        "operation": "equal",
        "right": "BINANCE"
      }
    },
    {
      "expression": {
        "left": "exchange",
        "operation": "equal",
        "right": "OKX"
      }
    }
  ]
}
```

Эта OR-группа добавляется внутрь общего AND вместе с CEX-фильтром.

### Числовые операции

Проверенные рабочие операции:

- `egreater` - больше или равно;
- `eless` - меньше или равно;
- `in_range` - диапазон.

Пример minimum volume:

```json
{
  "expression": {
    "left": "24h_vol|5",
    "operation": "egreater",
    "right": 1000000
  }
}
```

Пример диапазона цены:

```json
{
  "expression": {
    "left": "close",
    "operation": "in_range",
    "right": [1, 10]
  }
}
```

Операция `greater` тоже работала, но в скрипте используется `egreater`, чтобы не терять граничное значение.

## 8. Авторизация

Публичная CEX-таблица обычно доступна без логина. Но TradingView может отдавать account-specific данные или требовать authenticated context для отдельных возможностей.

Логин/пароль в скрипт не встроены намеренно:

- TradingView login может требовать 2FA;
- может появляться captcha;
- могут быть browser/device checks;
- прямой password-login был бы менее устойчивым и более рискованным.

Вместо этого используется cookie-based auth, как у браузера.

Поддержанные способы:

1. Raw Cookie header через аргумент:

```powershell
python .\tradingview_cex_exporter.py --cookie "sessionid=...; tv_ecuid=..." -o cex.csv
```

2. Raw Cookie header через env:

```powershell
$env:TRADINGVIEW_COOKIE='sessionid=...; tv_ecuid=...'
python .\tradingview_cex_exporter.py -o cex.csv
```

3. Файл cookies:

```powershell
python .\tradingview_cex_exporter.py --cookie-file .\cookies.txt -o cex.csv
```

`--cookie-file` принимает:

- Netscape cookies;
- JSON list вида `[{"name":"sessionid","value":"..."}]`;
- JSON dict вида `{"sessionid":"..."}`;
- raw Cookie header в обычном текстовом файле.

Проверка cookies:

```powershell
python .\tradingview_cex_exporter.py --cookie-file .\cookies.txt --check-auth --limit 1
```

`--check-auth` делает GET на страницу CEX screener и смотрит HTML-класс authenticated/not-authenticated.

## 9. Как работает скрипт

Основной файл:

`tradingview_cex_exporter.py`

### 9.1 CLI parsing

Функция:

```python
parse_args()
```

Отвечает за аргументы:

- output path;
- лимит строк;
- page size;
- delay;
- timeout/retries;
- фильтры;
- cookies;
- columns;
- sort;
- dry-run;
- verbose.

### 9.2 Колонки

Колонки описаны через dataclass:

```python
@dataclass(frozen=True)
class ColumnSpec:
    header: str
    api_name: str
```

По умолчанию используется `DEFAULT_COLUMNS`.

Если пользователь передает `--columns`, скрипт:

1. добавляет системную колонку `symbol_id`;
2. разбирает comma-separated список;
3. применяет aliases из `FIELD_ALIASES`;
4. отправляет в API реальные TradingView field names.

Пример:

```powershell
python .\tradingview_cex_exporter.py --columns ticker,exchange,price,volume_24h -o small.csv
```

Будет отправлено:

```json
["name", "exchange", "close", "24h_vol|5"]
```

### 9.3 Построение filter2

Функция:

```python
build_filter2(args)
```

Всегда начинает с:

```json
centralization == cex
```

Затем добавляет пользовательские фильтры:

- exchange;
- type;
- base;
- quote;
- price range;
- change range;
- volume range;
- volume change range;
- extra `--filter-json`.

`--type perpetual` нормализуется в `swap`, потому что API возвращает perpetual contracts как `type = swap`.

### 9.4 Cookies/session

Функции:

```python
read_cookie_file()
apply_auth()
make_session()
check_auth()
```

`make_session()` создает `requests.Session()` с браузероподобными headers:

- `Accept`;
- `Content-Type`;
- `Origin`;
- `Referer`;
- `User-Agent`.

Cookies добавляются либо в `session.cookies`, либо в raw `Cookie` header.

### 9.5 Scanner payload

Функция:

```python
build_payload(args, specs, offset, page_size)
```

Собирает:

- `columns`;
- `filter2`;
- `markets: ["crypto"]`;
- `options.lang`;
- `range`;
- `sort`;
- optional `symbols` при `--ticker`.

Если передан `--ticker BINANCE:BTCUSDT`, добавляется:

```json
{
  "symbols": {
    "tickers": ["BINANCE:BTCUSDT"],
    "query": {
      "types": []
    }
  }
}
```

### 9.6 HTTP request/retry

Функция:

```python
post_scan()
```

Делает POST на:

```text
https://scanner.tradingview.com/crypto/scan
```

Поведение:

- retry loop до `--retries`;
- HTTP 5xx считается retryable;
- HTTP 4xx падает сразу через `raise_for_status`;
- JSON error вида `{"error": "Unknown field ..."}` превращается в exception;
- между retry есть backoff.

### 9.7 Пагинация

Функция:

```python
export_csv()
```

Пагинация идет через `range`:

```json
"range": [offset, offset + page_size]
```

После каждого ответа:

1. читается `totalCount`;
2. берется массив `data`;
3. строки пишутся в CSV;
4. `exported += len(rows)`;
5. цикл заканчивается, если:
   - достигнут `--limit`;
   - `rows` пустой;
   - `exported >= totalCount`.

Между страницами используется `--delay`, по умолчанию `0.15` секунды.

### 9.8 CSV serialization

CSV пишется через стандартный `csv.DictWriter`.

По умолчанию:

- encoding: `utf-8-sig`;
- delimiter: `,`;
- newline корректно передается через `newline=""`.

`utf-8-sig` выбран, чтобы CSV лучше открывался в Excel на Windows.

## 10. Примеры запуска

Выгрузить первые 1000 строк:

```powershell
python .\tradingview_cex_exporter.py -o cex.csv --limit 1000 --verbose
```

Выгрузить всю CEX-таблицу:

```powershell
python .\tradingview_cex_exporter.py -o cex_all.csv --verbose
```

BINANCE spot USDT с объемом от 1m:

```powershell
python .\tradingview_cex_exporter.py -o binance_spot_usdt.csv --exchange BINANCE --type spot --quote USDT --min-volume 1000000
```

Несколько бирж:

```powershell
python .\tradingview_cex_exporter.py -o top.csv --exchange BINANCE,OKX,BYBIT --limit 500
```

Проверить payload:

```powershell
python .\tradingview_cex_exporter.py --exchange BINANCE --type spot --dry-run
```

## 11. Ограничения текущего экспортера

Текущий скрипт является batch exporter, а не listener:

- запускается вручную или по расписанию;
- каждый запуск пишет новый CSV;
- не хранит состояние между запусками;
- не считает diff между старыми и новыми значениями;
- не отправляет события;
- не дедуплицирует изменения.

Для постоянного наблюдения нужен отдельный listener layer. План добавления описан в `PLAN.md`.

## 12. Что важно не сломать при развитии

1. Не менять endpoint на несуществующий `cex/scan`.
2. Всегда оставлять permanent filter `centralization == cex`.
3. Не заменять `24h_vol|5` на CMC/Value.Traded поля без повторной проверки.
4. Сохранять cookie-based auth, а не добавлять password login как основной способ.
5. Не делать polling слишком частым без rate limiting/backoff.
6. Для listener хранить состояние отдельно от CSV-экспорта.
