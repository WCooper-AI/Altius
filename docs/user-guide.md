# User guide

Altius follows trading rules **you** write. It never places a trade and never tells you what
to trade. Every number you see is a fact about the rules on the prices they were run on,
nothing more.

## The five stages

Every strategy goes through the same five stages, and its page shows where it is and the one
thing to do next:

| Stage | What it means | Where you do it |
|---|---|---|
| **Research** | The rules are written and valid | The strategy's page (guided settings or the full rule builder) |
| **Backtest** | The rules have been run on past prices | *Backtest* tab on the strategy's page |
| **Validate** | A robustness check has been run on those prices | *Luck check* tab, the train/test split on the Backtest tab, or the Optimiser's kept-back check |
| **Paper trade** | A forward test is following the rules on live prices, with no money | *Start forward test* on the strategy's page or the dashboard |
| **Monitor** | Alerts are on, and live trades can be compared with the backtest's usual range | *Phone alerts on* on the strategy's page; the forward test's page |

The strip is facts only: done, when, and whether the rules have changed since a test. It
never says a result is good or bad. The dashboard shows the same five steps as a checklist
until each has been reached once.

## Your first five minutes

1. Open Altius (`http://localhost:8321` on a self-hosted install).
2. Press **Try it with sample data**. This saves a draft strategy on the built-in *Sample data*
   market: made-up prices generated on your computer, so nothing needs downloading and nothing
   it shows applies to a real market. You can also pick *Sample data* as the market for any
   guided idea.
3. Press **Test on past prices**. The results are written out in sentences, with an equity
   curve, the trades, and a *How this was tested* record of exactly what was run.
4. Open the **Luck check** tab and run it. It reshuffles the trades, re-runs the rules on
   made-up price paths, and reports how much of the result could be chance.
5. Press **Start forward test**. From now on every closed candle is checked and paper trades
   are recorded. **Phone alerts on** does the same and also sends you each signal.

Then create a strategy on a real market: *Strategies → Create*.

## Pages

- **Dashboard.** Running forward tests with balance, open position and a sparkline; a one-line
  summary; the getting-started checklist; a 24-hour timeline of market sessions and high-impact
  news.
- **Markets.** Candlestick charts that refresh every 30 seconds, with a strategy's signals and
  paper trades marked. Click the chart to set a **price alert** ("tell me when gold goes above
  2,700"), with repeat, cool-down and cross-only options. *Market stats* shows each market's
  typical moves.
- **Strategies.** The list with status, market and forward-test figures; search, filter and
  sort. *Create* opens the guided setup; *Build from scratch* opens the full rule builder.
  *Compare* runs several strategies side by side; *Test together* combines them on one
  balance (see [Strategy development](strategies.md)).
- **News.** The economic calendar: the US jobs report is built in, the weekly Forex Factory
  calendar is saved as it is fetched, and you can add your own events. A rule (and a guided
  switch) can keep a strategy out of the market around high-impact news.
- **Activity.** Everything that happened: signals, trades opened and closed, alerts that failed,
  price alerts, news reminders, settings changes. The bell in the top bar shows what is new.
- **Journal.** Log the trades you actually placed, link each one to the paper trade it came
  from, and see your slippage against the paper price. *Log real trade* on a paper trade
  pre-fills the entry. Tags, setup, "followed the plan" and mood fields, breakdowns, and a CSV
  export.
- **Settings.** Alert channels, how often to check, the price-data source, your own price
  files, appearance, the weekly report, backup and restore, and where secrets are kept.

## Strategy status

| Status | Meaning |
|---|---|
| Draft | Saved for testing. Nothing is checked live. |
| Forward testing | A forward test is running on live prices, with no alerts. |
| Alerts on | Checked on every new candle; each signal is sent to your alert channels. A forward test runs alongside. |
| Paused | Alerts were on and have been turned off. |
| Needs fixing | The file could not be loaded; the list shows the problem. |

Editing the rules of a strategy with a running forward test asks what to do with the test:
restart it on the new rules, keep it on the old ones, or stop it.

## Alerts

Channels: ntfy (phone push, free), Telegram, Discord, Slack, email (your own SMTP account)
and any webhook. Turn a channel on in *Settings* and press *Send test alert*. Each alert says
which strategy fired, the market, the price and the stop and target the rules gave.

Besides signals you can get price alerts, a warning when a forward test's drawdown passes a
level you set, a warning when price data stops arriving, a daily summary, and a weekly report
(email gets a formatted version; other channels a short text one).

Alerts are only sent while Altius is running. A laptop that sleeps sends nothing until it
wakes; see [Installation](install.md) for the background service and Docker.

## Backups

*Settings → Backup* writes one zip with your strategies, settings, forward tests, journal and
price files, and restores one. A safety copy is made before every restore, and a restore that
fails part-way is rolled back. A **shareable backup** blanks passwords, tokens, webhook
addresses and ntfy topics so you can send a setup to someone else.

## Where things are

Self-hosted, everything is in your `TradingAlerts` folder: `config.yaml`, `strategies/*.yaml`,
`data/` (the SQLite database, the price cache, your CSV files) and `reports/`. Nothing is
sent anywhere except to the price source and the alert channels you chose. The hosted edition
keeps the same things on its server for your account.
