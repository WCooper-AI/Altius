# Backtesting methodology

The backtester and the forward tests share one simulator (`simulator.py`), so a forward
test is the backtest continued on prices that arrive later. This page says exactly what it
does. Backtests are simulations of past prices under stated assumptions; they are not a
forecast.

## Signals and fills

1. Rules are checked on a candle's **close**. Unfinished candles are never used, and
   automated lookahead tests check that no rule can see a later candle.
2. Entries fill at the **next candle's open**, buying at the ask and selling at the bid. The
   spread is each candle's real bid/ask spread when the data has it (OANDA, Dukascopy),
   otherwise the strategy's spread setting.
3. Stops and targets trigger at the prices you would get: a long position's stop or target
   when the **bid** reaches it, a short's when the **ask** does. A candle that opens beyond the
   stop fills at that open (gap risk), and one that opens beyond the target fills at that
   better open (a target is a limit order). A stop closer than the spread is already passed
   when the trade fills, so the trade closes straight away at the bid (long) or ask (short).
4. If a stop and a target both fall inside one candle, **finer candles** (for example 1-hour
   inside 4-hour or daily) decide which came first, at a finer candle's open if it opened
   beyond the level. Without finer data the stop is assumed first.
5. An exit rule closes at the next open; "close after N candles" closes at the Nth candle's
   close. One position per strategy at a time.
6. 4-hour and daily candles start at **17:00 New York** by default, like most CFD and FX
   broker charts (`market.candle_alignment: utc` changes this). They keep that hour through
   the clock changes, so the daily candle that spans one is 23 or 25 hours long, as on those charts.

## Costs

| Cost | How it is charged |
|---|---|
| Spread | Prices in the data are mid prices; you buy at the ask (mid plus half the spread) and sell at the bid (mid minus half), from the candle's own spread or the setting |
| Slippage | On every fill except a target (entries, stops, exit rules, time exits, close-outs), as points, as a % of the price, or as a % of the 14-candle ATR known at the candle's open, so it grows with volatility |
| Overnight funding | For every night a position is held, at 22:00 UTC, weekends included, at the yearly rate |
| Borrow fee | Like funding, on short positions in stocks only |
| Commission | Money per fill and/or % of the fill value, on entry and exit; the entry fee is taken from equity at the fill |

Each trade records `spread_cost`, `slippage_cost`, `commission`, `funding`, `borrow` and
`financing` (funding plus borrow), and every backtest totals them, so a shortfall against a
gross result can be traced to a cost rather than to the rules.

## Position size

Size is set so that the stop loses `risk_per_trade_pct` of the balance: size = money at
risk ÷ (stop distance × point value), rounded down to the size step and capped by the
minimum and maximum size. With `compound: true` (the default) the balance is the current
one; with `false` it is the starting balance. The practice balance, risk % and currency
are yours to fill in.

With `size_currency: market` the size is in the market's own currency and every amount is
converted at the daily exchange rate known at the time of the fill (`fxrates.py`; the pair's
previous daily close), or at a fixed `fx_rate`. The trade records the rate used.

## Loss limits and margin

- `max_daily_loss_pct` and `max_weekly_loss_pct`: once a day or a week has lost that much, no
  new trades for the rest of it. `max_drawdown_pct`: no new trades once the balance is that
  far below its high. Open positions are managed as usual. Blocked signals are counted
  (`blocked_by_limits`) and shown.
- `margin_pct` holds that share of a position's value as margin (built-in markets carry a
  typical retail rate). `max_margin_pct` refuses a new trade that would take the margin held
  past that share of the balance. `close_out_pct` closes everything when balance plus open
  P&L falls below that share of the margin held (exit reason `close_out`). Close-out applies to
  one strategy's own account; the shared-account portfolio has a margin cap but no close-out.

## What the backtest reports

Metrics (`backtest.compute_metrics`): total return and final balance, number of trades, win
rate, profit factor, expectancy, average win and
loss, the largest win and loss, longest winning and losing streaks, average hold, maximum
drawdown and its length and recovery, longest time under water, Sharpe and Sortino
(annualised by the candles actually seen per weekday, capped at 24 hours; daily data uses
252), CAGR and Calmar for tests of a year or more, the average of the worst 5% of days, the
biggest fall and rise per day, week and month, trades by weekday and by trading session,
monthly returns, R-multiple and hold-time histograms, each cost's total, repaired candles, a
buy-and-hold comparison, and the equity curve. A backtest with no trades explains which
condition never matched.

Numbers are facts about these rules on these prices. The interface explains what each one
measures; it does not judge them.

## The run record

Every backtest, comparison, optimiser run and luck check carries a **run record** ("How this
was tested"), so the same test can be run again and give the same answer: engine version, a
fingerprint of the rules and the saved version they match, market, symbol and data source,
candle size, the period actually covered, where fill prices and the spread came from, every
cost and sizing setting, the train/test split, the random seed, a fingerprint of the candles,
and the data-quality findings on them. `tradealerts check-data` reports the same findings
from the command line.

## Accounting checks

`tradealerts.invariants.check(result)` lists anything a result gets wrong: balance steps that
do not match the trades, costs that do not add up, positions open past the data, sizes
below the minimum, and so on. The test suite runs it on hand-worked reference scenarios and
on randomly generated strategies, and the simulator refuses to continue when a check fails.

## Known limits

- Fills are modelled, not real: a broker's spread, slippage and funding will differ.
- One position per strategy; no partial exits or pyramiding.
- Price data is only as good as its source: see [Data providers](data-providers.md).
- Past results, including every check in [Statistical validation](validation.md), do not
  predict future results.
