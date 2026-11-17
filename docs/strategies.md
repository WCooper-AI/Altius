# Strategy development

A strategy is one market, one candle size, a direction, an entry rule, an exit (stop, target,
time limit, optional exit rule), risk settings and costs. Altius writes it as a YAML file
(see the [YAML reference](yaml-reference.md)); you can build it three ways.

## Guided setup

*Strategies → Create*: pick a market, pick an idea, fill in a few plain-language settings.
The rule is written out as a sentence as you go, and blanks are listed until every setting is
filled. Every setting starts blank: nothing is suggested for you.

| Idea | What it does |
|---|---|
| Buy the dip | Buys after the price has fallen a set % from its recent high, while the longer trend is up |
| Follow the trend | Buys when the price closes above its highest point of the last few candles; exits below a shorter-term low |
| Bounce from oversold | Buys when RSI crosses back up from an oversold level |
| Moving average crossover | Buys when a faster average crosses above a slower one |
| Opening range breakout | Buys when the price breaks above the first minutes of the session (markets with an opening time only) |

Each idea has a sell version. Common settings: stop (in ATR, % or points), target (a multiple
of the stop, or none), how long to hold at most, and a news switch.

A guided strategy opens in the simple view. *Edit the full rules* turns it into a custom
strategy in the full builder.

## The rule builder

*Build from scratch* opens the full editor:

- **Groups:** `all` of, `any` of, and `not`. Groups nest. Conditions can be moved up and down
  and negated one by one.
- **Comparisons:** two operands and an operator (`<`, `<=`, `>`, `>=`, `==`, crosses above,
  crosses below).
- **Operands:** a price field (`open`, `high`, `low`, `close`, `volume`), a number, or an
  indicator with its parameters; any operand can be shifted back `n` candles, multiplied, or
  have a number added. Twenty-plus indicators: moving averages, MACD, RSI, ATR, Bollinger and
  Keltner bands, highest/lowest, distance from the recent high or low, z-score, volatility,
  the session opening range.
- **Time windows and weekdays:** only fire between two times (in a time zone you pick) or on
  chosen weekdays.
- **Exits:** stop and target in ATR, %, points, or a price from your rules (for example the
  opening-range low); a maximum number of candles to hold; an optional exit rule.
- **Count signals** shows how often the entry rule fired on past data before you backtest.
- **Advanced: Edit YAML** shows the file and keeps it in sync with the form.

The rules are shown as a plain-English sentence throughout. Conditions are checked on closed
candles only; see [Backtesting methodology](backtesting.md) for how fills work.

## Markets and instruments

The built-in markets are indices (US 500, US 100, Wall Street 30, Germany 40, UK 100,
Netherlands 25), gold, silver, oil, EUR/USD, GBP/USD, EUR/GBP, Bitcoin, four US stocks (SPY,
AAPL, MSFT, NVDA) and **Sample data**. Each one knows its price step, point value, typical
spread, overnight funding rate, margin rate, currency and trading hours, so a strategy's blank
risk and cost settings fall back to them; the strategy page shows the line it uses.

Stocks trade in exchange hours only (exact holidays with the `stocks` extra), charge a
commission per fill and a borrow fee on short positions, and are fetched from Yahoo or
Dukascopy only.

In the full editor you can also type a custom symbol: a Dukascopy instrument name, a Yahoo
symbol, an OANDA instrument, or one of your CSV files. Custom symbols have no built-in
defaults, so fill in the point value and spread yourself.

**Sample data** is made-up prices generated on your computer (a fixed series that extends as
time passes, so backtests on it repeat exactly). It is for trying the tool; results on it say
nothing about any real market.

## Testing tools on the strategy page

- **Backtest** over the last 1, 2, 3, 5 or 10 years or a date range, with an optional
  train/test split.
- **Compare settings** runs the same backtest with one setting stepped through a range.
- **Optimiser** runs every combination of up to three settings in the background, with a
  heatmap, a hold-out period and walk-forward. *Apply settings* fills the form; you still
  save.
- **Luck check** asks how much of a backtest could be chance.

What each one measures is in [Statistical validation](validation.md).

## Versions and history

Every save keeps a version (the most recent fifty). *History* on the strategy page shows them
with the note you gave, lets you restore one, and **Compare results** backtests two to four
versions side by side over the same period, with a *Change* column when there are two.

## Sharing

*Duplicate* makes a draft copy. *Export* writes the rules as a file and *Share link* makes a
link that carries them; imports always arrive as drafts and never include results. The
command line can also create and validate files: `tradealerts new`, `tradealerts validate`.

## Testing strategies together

*Strategies → Compare* runs several strategies over the same period and lines up their
metrics. *Test together* (the portfolio page) combines them on one balance: the combined
curve, each strategy's share of the profit, how much was at risk at once, by-market exposure,
how alike their daily results are, and a diversification figure. Weighting can be equal,
inverse volatility or custom. The **shared account** replays the trades on one compounding
balance with caps on open trades, open risk and margin, and reports what was skipped.
