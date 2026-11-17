# YAML reference

The web interface writes strategy files for you and reads the same files back, so editing
by hand is optional. Files live in `strategies/` (self-hosted: inside your `TradingAlerts`
folder). `tradealerts validate` checks them.

## A strategy file

```yaml
name: My pullback rule
description: Optional note to yourself
template: false            # true (or missing) = draft, backtest only; false = can send alerts
enabled: true              # with template: false, false pauses the alerts

market:
  symbol: us500            # a built-in market key with provider: auto (see below)
  label: US 500            # shown in alerts
  provider: auto           # auto, dukascopy, yahoo, oanda or csv
  timeframe: 4h            # 5m, 15m, 30m, 1h, 2h, 4h or 1d
  # lookback_days: 720     # history to load; the default depends on the candle size
  # tz: UTC                # time zone of a CSV file's naive timestamps
  # candle_alignment: new_york   # or utc: where 4h and daily candles start

direction: long            # or short

entry:                     # checked on each closed candle
  all:
    - left: {indicator: pct_from_high, period: 30}
      op: "<="
      right: -3
    - left: close
      op: ">"
      right: {indicator: sma, period: 200}

exit:
  stop: {atr: 2, atr_period: 14}
  target: {r_multiple: 2}  # or omit for no target
  max_bars: 30             # close after this many candles
  when:                    # optional exit rule
    left: {indicator: rsi}
    op: ">"
    right: 70

risk:
  account_size: 1000
  risk_per_trade_pct: 0.5
  currency: GBP

costs:
  spread: 0.6              # points; blank uses the built-in market's typical spread

alerts:
  enabled: true
  priority: high           # min, low, default, high, urgent
```

A strategy made with the guided setup also carries a `recipe` block (the idea, the market
and the settings); the interface keeps it as long as the rules still match the idea.

### market

| Key | Meaning |
|---|---|
| `symbol` | With `provider: auto`, a built-in key: `us500`, `us100`, `us30`, `de40`, `uk100`, `nl25`, `gold`, `silver`, `oil`, `eurusd`, `gbpusd`, `eurgbp`, `btc`, `spy`, `aapl`, `msft`, `nvda`, `sample`. Otherwise a Dukascopy instrument name, a Yahoo symbol, an OANDA instrument, or a CSV path inside the data folder. |
| `provider` | `auto` fetches a built-in market from the source chosen in Settings; `dukascopy`, `yahoo`, `oanda` or `csv` name one source. |
| `timeframe` | `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `1d`. |
| `lookback_days` | How much history a live check loads. Backtests load what the period needs. |
| `tz` | Time zone for naive timestamps in a CSV file (default UTC). |
| `candle_alignment` | `new_york` (4h and daily candles start at 17:00 New York, like most CFD charts) or `utc`. |

### Conditions

| Form | Meaning |
|---|---|
| `{left: A, op: OP, right: B}` | Compare two operands. `OP`: `<` `<=` `>` `>=` `==` `crosses_above` `crosses_below` |
| `{all: [...]}` / `{any: [...]}` | Every / at least one condition true |
| `{not: {...}}` | Invert a condition |
| `{time_between: ["10:00", "15:30"], tz: America/New_York}` | Candle start time in a window (start inclusive, end exclusive) |
| `{weekdays: [mon, tue, wed, thu, fri]}` | Day of week (optional `tz`) |

### Operands

| Form | Meaning |
|---|---|
| `close`, `open`, `high`, `low`, `volume` | Price fields |
| `1.5` | A number |
| `{field: close, shift: 1}` | The previous candle's close |
| `{indicator: NAME, ...params}` | An indicator (below) |
| any mapping + `shift: n`, `scale: x`, `add: y` | `n` candles ago, multiplied by `x`, plus `y` |

### Indicators

| Name | Parameters (defaults) | Description |
|---|---|---|
| `sma` | period=20, source='close' | Simple moving average |
| `ema` | period=20, source='close' | Exponential moving average |
| `macd` / `macd_signal` / `macd_hist` | fast=12, slow=26, signal=9, source='close' | MACD line, signal line, histogram |
| `rsi` | period=14, source='close' | Wilder's RSI, 0–100 |
| `pct_change` | period=1, source='close' | % change over `period` candles |
| `zscore` | period=20, source='close' | Standard deviations from the rolling mean |
| `atr` | period=14 | Average true range, in points (Wilder smoothing) |
| `atr_pct` | period=14 | ATR as % of the close |
| `true_range` | | True range of each candle |
| `volatility` | period=20, source='close' | Standard deviation of candle returns, % |
| `bollinger_upper` / `bollinger_lower` | period=20, std=2.0, source='close' | Bollinger bands |
| `keltner_upper` / `keltner_lower` | period=20, atr_period=10, multiplier=2.0 | Keltner channels |
| `highest` | period=20, source='high' | Highest value of the last `period` candles, including this one |
| `lowest` | period=20, source='low' | Lowest value of the last `period` candles, including this one |
| `pct_from_high` | period=20 | % the close is below the `period`-candle high (≤ 0) |
| `pct_from_low` | period=20 | % the close is above the `period`-candle low (≥ 0) |
| `opening_range_high` / `opening_range_low` | session_start='09:30', minutes=30, tz='America/New_York' | The session's opening range; empty until the range has finished |

For a channel breakout compare against the *previous* candles, e.g.
`{indicator: highest, period: 55, shift: 1}`; otherwise the current candle is part of its own
high.

### exit

| Key | Meaning |
|---|---|
| `stop` | `{atr: 2, atr_period: 14}`, `{percent: 1.5}`, `{points: 40}` or `{level: <operand>}` (a price from your rules) |
| `target` | The same forms, or `{r_multiple: 2}` (twice the stop distance). Omit it for no target. |
| `max_bars` | Close at the close of the Nth candle after entry |
| `when` | A condition; when true on a closed candle the position closes at the next open |

### risk

| Key | Meaning |
|---|---|
| `account_size` | Starting balance for backtests and paper trading |
| `risk_per_trade_pct` | The % of the balance lost if the stop is hit; this sets the size |
| `currency` | `GBP`, `USD` or `EUR` |
| `point_value` | Money per point per 1 unit of size (built-in markets know theirs) |
| `size_step`, `min_size`, `max_size` | How sizes are rounded and capped, like a broker's ticket |
| `compound` | `true` sizes off the current balance, `false` off the starting balance |
| `max_daily_loss_pct`, `max_weekly_loss_pct` | No new trades for the rest of a day or week that has lost this % |
| `max_drawdown_pct` | No new trades once the balance is this % below its high |
| `size_currency` | `account` (size is money per point in the account currency, like a spread bet) or `market` (size is in the market's own currency, converted at the exchange rate of the day) |
| `market_currency` | The market's currency when `size_currency: market` is used with a custom symbol |
| `fx_rate` | A fixed rate (account money per 1 unit of market money); blank downloads the daily rate |
| `margin_pct` | % of a position's value held as margin (built-in markets know a typical rate) |
| `max_margin_pct` | No new trade that would take the margin held past this % of the balance |
| `close_out_pct` | Close everything when balance plus open P&L falls below this % of the margin held |

### costs

| Key | Meaning |
|---|---|
| `spread` | Points added to every fill when the data has no spread of its own |
| `use_data_spread` | `true` (default) uses each candle's real bid/ask spread when the data has it (OANDA, Dukascopy) |
| `funding_pct` | Yearly overnight funding rate, charged for every night a position is held |
| `commission`, `commission_pct` | Money per fill, and % of the fill value, charged on entry and exit (stocks) |
| `borrow_pct` | Yearly borrow fee on short positions, charged nightly (stocks) |
| `slippage`, `slippage_pct` | Points, or % of the price, lost on every market fill |
| `slippage_atr_pct` | % of the 14-candle ATR lost on every market fill, so slippage grows with volatility |

### alerts

| Key | Meaning |
|---|---|
| `enabled` | Whether this strategy's signals are sent |
| `priority` | `min`, `low`, `default`, `high` or `urgent` (ntfy priority; other channels ignore it) |

## config.yaml

```yaml
strategies_dir: strategies   # your strategy files
data_dir: data               # live state (SQLite), the price cache and your CSV files
reports_dir: reports         # backtest reports from the command line
poll_minutes: 15             # how often to look for newly closed candles
log_level: info

data:                        # price data for the built-in markets
  source: dukascopy          # dukascopy (free, no account), yahoo, or oanda (needs a token)
  # oanda_token: ${OANDA_TOKEN}
  # oanda_environment: practice

notifiers:
  - type: console            # prints alerts in the server log
  - type: ntfy
    enabled: false
    server: https://ntfy.sh
    topic: ""
    token: ""
  - type: telegram
    enabled: false
    token: ""
    chat_id: ""
  - type: discord
    enabled: false
    webhook_url: ""
  - type: slack
    enabled: false
    webhook_url: ""
  - type: webhook            # POSTs {title, body, priority, tags} as JSON
    enabled: false
    url: ""
  - type: email
    enabled: false
```

- Any value can come from an environment variable: `topic: ${NTFY_TOPIC}`, with a default as
  `${NTFY_TOKEN:-}`.
- Where the computer's password store is used (the desktop app, or `tradealerts serve` with
  the `keychain` extra), secrets are replaced by a `keychain:<name>` reference; see
  [Security](security.md).
- `appearance` and `report` blocks are written by the interface (theme, accent, density; the
  weekly report's day, hour and time zone).
