# TradingView CEX CSV Exporter

Экспортирует основную таблицу `https://ru.tradingview.com/cex-screener/` в CSV через тот же scanner API, который использует страница. Постоянный фильтр CEX уже встроен: `centralization == cex`.

## Установка

```powershell
python -m pip install -r requirements.txt
```

## Быстрый запуск

```powershell
python .\tradingview_cex_exporter.py -o cex.csv --limit 1000 --verbose
```

Без `--limit` или с `--limit 0` скрипт выгружает все найденные строки.

## Фильтры

```powershell
python .\tradingview_cex_exporter.py -o binance_spot_usdt.csv --exchange BINANCE --type spot --quote USDT --min-volume 1000000
```

Поддерживаются базовые фильтры:

- `--exchange BINANCE` можно повторять или передавать через запятую.
- `--type spot|swap|futures|perpetual`; `perpetual` мапится в API-тип `swap`.
- `--base BTC`, `--quote USDT`.
- `--min-price`, `--max-price`.
- `--min-change`, `--max-change`.
- `--min-volume`, `--max-volume`.
- `--min-volume-change`, `--max-volume-change`.
- `--ticker BINANCE:BTCUSDT` для конкретных TradingView tickers.

Для нестандартных фильтров есть `--filter-json`. Можно передать JSON-выражение, список operands или полный `filter2`, в том числе из файла через `@filters.json`.

## Авторизация

Публичная CEX-таблица обычно экспортируется без логина. Если нужны cookies аккаунта TradingView, передайте их так:

```powershell
$env:TRADINGVIEW_COOKIE='sessionid=...; tv_ecuid=...'
python .\tradingview_cex_exporter.py -o cex.csv --check-auth --verbose
```

Или через файл:

```powershell
python .\tradingview_cex_exporter.py --cookie-file .\cookies.txt -o cex.csv --check-auth
```

`--cookie-file` принимает Netscape cookies, JSON export вида `[{"name":"...","value":"..."}]`, JSON-словарь или raw Cookie header.

## Колонки

По умолчанию экспортируются обзорные колонки CEX-скринера:

`symbol_id,ticker,description,exchange,type,base_currency,quote_currency,price,change_24h_pct,volume_24h,volume_24h_change_pct,technical_rating_1d`

Можно задать свои API-колонки:

```powershell
python .\tradingview_cex_exporter.py --columns ticker,exchange,price,volume_24h -o small.csv
```

Проверить payload без запроса:

```powershell
python .\tradingview_cex_exporter.py --exchange BINANCE --dry-run
```
