# Data providers

Altius does not ship price data. Each person downloads prices from the source they choose,
for their own use, or supplies their own CSV files. The one exception is **Sample data**,
which is generated on your computer.

## Sources for the built-in markets

Choose the source in *Settings → Price data*. Strategies on built-in markets
(`provider: auto`) follow that choice.

| Source | Account? | What you get |
|---|---|---|
| **Dukascopy** (default) | None, no token | CFD-style bid and ask prices with years of history, so each candle's real spread is used. Fetched with [dukascopy-python](https://github.com/Eghosa-Osayande/dukascopy) (MIT). The first download of a market takes a while; later only new candles are fetched. |
| **Yahoo Finance** | None, no token (needs the `yahoo` extra) | Delayed exchange prices. About 2 years of hourly history and about 60 days below that. Index futures (ES=F, NQ=F) jump at each contract roll, and there is no spread data. Personal use only. |
| **OANDA** | Free practice account and API token | Real CFD bid/ask candles from a broker. Enter the token in Settings after choosing OANDA, or set `OANDA_TOKEN` and use `oanda_token: ${OANDA_TOKEN}` in `config.yaml`. Some markets depend on your region; stocks are not available. |

Dukascopy and Yahoo are free public feeds with no service guarantee, and their terms generally
cover personal use. Dukascopy sometimes blocks automated downloads; if so, use their
historical-data page in a browser and load the CSV.

## Custom symbols

In the full editor a market can be any Dukascopy instrument name (for example
`INSTRUMENT_IDX_ASIA_E_N225JAP`), a Yahoo symbol, an OANDA instrument, or a CSV file, with
`provider` set to the source. Custom symbols have no built-in point value, spread, funding or
margin, so fill those in.

## CSV files

Upload files in *Settings → Your price files* (or with the Upload button in a strategy's
market picker), then pick "My own CSV file". Self-hosted, you can also put files anywhere
inside the data folder and type the path.

- Columns: a time column named `timestamp`, `time`, `date` or `datetime`, then `open`,
  `high`, `low`, `close`, and optionally `volume` and `spread`.
- Times can be dates, date-times, or Unix timestamps in seconds or milliseconds. Naive
  date-times are read in the strategy's `market.tz` (default UTC).
- Finer candles are combined into the strategy's candle size and also used to decide the order
  of a stop and a target inside one candle.

`tradealerts sample-data` writes a CSV of made-up candles for offline tests of the file path.
Dukascopy history can be exported to CSV with [dukascopy-node](https://www.dukascopy-node.app).

## Sample data

The built-in **Sample data** market (`sample`) is a fixed series of made-up 15-minute
candles, generated from a fixed seed on your computer and cut off at the current time. The
candles before any moment are always the same, so a backtest on it repeats exactly, and a
forward test on it sees new candles arrive. It is for trying the tool. Every result on it
carries a note saying so, and nothing it shows applies to a real market.

## Caching

Downloads are kept under `data/cache`, as Parquet files when `pyarrow` is installed
(`tradealerts[parquet]`; the cache keeps revisions) and as CSV otherwise. Later requests
fetch only the candles after the cached ones. Backtest results for the same request are also
kept in memory while the app runs.

## Data-quality checks

Every backtest reports what it found in the candles ("How this was tested"), the Markets page
shows a strip for the live candles, and `tradealerts check-data STRATEGY` prints the same from
the command line:

| Finding | Meaning |
|---|---|
| gap / missing_bars | Candles missing inside the period (weekends and exchange closures ignored) |
| stale | The last candle is well behind the current time (live data only) |
| repaired | Candles whose high or low did not contain the open or close were widened when loaded |
| disordered | Timestamps that went backwards or repeated |
| frequency | Candles at a different spacing from the strategy's candle size |
| jump | An unusual price jump between candles, likely a bad tick |
| roll | A step change on a futures symbol (ends `=F`), likely the contract rolling to the next one |
| split | An overnight move close to a common stock-split ratio (2:1, 3:2, and so on); prices are not adjusted for it |
| dividend | Small, regularly-spaced overnight drops on a stock, the pattern an unadjusted feed leaves on ex-dividend dates; prices are not adjusted for it |
| timezone | Timestamps without a time zone |
| spread | Candles without a spread when the file has a spread column |

The split and dividend checks only look at the price: Altius never has a real corporate-actions
calendar to confirm them, so they are reported as possibilities ("possible split"), never acted
on, and no price is ever changed because of one.

Every result also carries a fingerprint of the candles it ran on, so two runs can be checked
against each other.

## What "How this was tested" shows about the data source

Every backtest's run record states, in plain terms, the price source's own characteristics —
not settings you chose, but facts about where the candles came from:

- **Price**: whether the candles are a mid price (from bid and ask), the last traded price, or
  whatever a CSV file contains.
- **Adjusted or not**: whether the feed already builds in stock splits and dividends (CFD and
  FX feeds don't need to, since there's nothing to adjust) or shows the raw, unadjusted price.
- **Spread data**: whether the feed has a real bid/ask spread per candle, or none.
- **Typical history available**: roughly how far back that source's data goes.

It also states the trading-hours assumption used for that market: the plain-English hours, how
the weekend (and, for CFD-style markets, the daily 17:00–18:00 New York break) is handled, and
whether that came from the real exchange calendar (holidays included) or the plain
Monday-to-Friday fallback timetable used when `exchange_calendars` isn't installed.

## Exchange rates

When a market is priced in a currency other than the account's, and `risk.size_currency:
market` is set, amounts are converted with the built-in currency pairs (EUR/USD, GBP/USD,
EUR/GBP) at the previous daily close known at each candle's open, or at a fixed
`risk.fx_rate`. The pair's candles come from the same source as the market's.

## Market hours

Indices, commodities and currencies are treated as open around the clock on weekdays, with
the daily 17:00–18:00 New York break; crypto every day. Stocks follow exchange hours and
holidays (exact with `tradealerts[stocks]`, otherwise Monday–Friday 09:30–16:00 New York).
The dashboard says when a forward test is waiting for its market to open.
