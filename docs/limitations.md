# Limitations and known issues

Plain, factual notes on what Altius does not do, what it approximates, and the low-severity
items the project's own audits found and left open. None of this is a verdict on any
strategy; it is about the tool itself. See also [Backtesting methodology, Known
limits](backtesting.md#known-limits) and [Statistical validation](validation.md) for what the
test results themselves can and cannot tell you.

## Simulations, not live trading

- **Backtests simulate past prices under stated assumptions**; they model fills, spread,
  slippage, funding and commission rather than replaying a real broker's own fills. See
  [Backtesting methodology](backtesting.md).
- **Paper (forward) trading is a simulation on live prices, not a real account.** It uses the
  same simulator as the backtester, so the same fill, cost and sizing assumptions apply; no
  order is ever sent to a broker. The app marks paper-trading figures as such on the
  dashboard, the forward-test page, the activity log and the weekly report.
- **There is no cross-check against a real broker's own statements yet.** Comparing fills,
  funding and candles against real account history needs that account's own statements; the
  project's own audits recorded this as not done. If you keep a broker account alongside
  Altius, the [journal](user-guide.md) lets you log its real fills next to the paper ones by
  hand.
- **Price data is only as good as its source.** Dukascopy, Yahoo Finance, OANDA and your own
  CSV files differ in history length, whether they are adjusted for stock splits and
  dividends, and whether real bid/ask spreads are available; see [Data
  providers](data-providers.md).

## Data-quality checks are heuristics, not a corporate-actions feed

The "possible split" and "possible dividend" notes on stock data look only at the price
pattern (an overnight move close to a common split ratio, or small regularly-spaced overnight
drops). Altius has no real corporate-actions calendar to confirm them, so both are reported as
possibilities only and nothing is ever adjusted automatically; see [Data providers, Data-quality
checks](data-providers.md#data-quality-checks).

## An equity-curve timing convention

Within one strategy's own backtest, each equity point is a labelling convention: it is stored
at the *start* of the candle it belongs to, but its value is the balance after that candle's
close. This does not affect a single strategy's own numbers. When *Test together* or the
shared account combines strategies on different candle sizes (for example a 1-day strategy
with a 1-hour one), the daily strategy's result can appear on the combined chart up to one
candle earlier than the hourly one's. Changing the convention would move every stored curve,
monthly table and report, so it is recorded rather than changed; see the project's quantitative
audit for the detail.

## Alerts: at-least-once delivery

A failed alert is retried a few times with a growing wait (see
[Troubleshooting](troubleshooting.md)). If a channel accepts a message but Altius crashes or
loses its connection before it finishes recording that success, the next retry resends it: the
channel may occasionally receive the same alert twice, rather than Altius ever silently
dropping one within its retry budget. Channels are not asked to de-duplicate.

## Low-severity items left open after the project's own audits

Each of these was found, judged low-impact, and left as a documented trade-off rather than
fixed; none has caused a reported problem.

- **Session cookies aren't individually revocable.** Signing out clears the browser's cookie,
  but a copied cookie stays valid for up to 7 days. Changing the password signs out every
  session at once; see [Security](security.md#password-and-sessions).
- **Behind a reverse proxy without `FORWARDED_ALLOW_IPS` set**, every visitor's failed
  sign-ins share one slow-down counter, so one person's wrong passwords can briefly lock out
  everyone behind the same proxy. See [Running on a server](self-hosting.md).
- **The desktop app's and `127.0.0.1`'s API have no password by default**, by design for a
  single-user machine: any other process running as you on that machine could call it.
- Two people uploading a CSV with the *same filename* to the same workspace at the same
  instant can occasionally see a confusing validation error (never data loss: the final file
  write is atomic).
- The optimiser's progress poll keeps querying the server for a short while after you
  navigate away from its page before a long run finishes; harmless, just an avoidable request
  every second or so.
- The unread-alerts count on the notification badge under-reports once there are more than
  100 unread items at once (shows a smaller number rather than "100+").
- `setup.sh`'s generated Linux systemd service file would not start if the install path
  contained a space (very unlikely on a standard install).
- A handful of very-low-impact modelling edges (same-candle stop/target order without finer
  data assumed conservatively against you; a fixed 22:00 UTC funding cut-off rather than each
  broker's own rollover time; the earliest usable foreign-exchange rate repeating backwards
  over a short history) are listed in the project's quantitative audit; none change a result
  by more than a rounding amount in the cases checked.

Self-hosted plan limits (maximum running forward tests, maximum price alerts) and the hosted
edition's own account and billing code are outside this repository; see [Licence and
editions](../README.md#licence-and-editions).
