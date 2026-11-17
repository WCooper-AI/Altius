# Unreleased

User-visible and behaviour changes from the project's own code, quantitative and security
audits and their follow-up sessions, not yet given a version number.

## Changes to existing backtest results

- **New York 4-hour and daily candles no longer drift an hour after a clock change.** They were
  cut every fixed 24 (or 4) hours of real time from the first day's 17:00 New York, so after
  each clock change they started an hour off, including a one-hour "daily candle" every weekend
  for roughly half of any multi-month test. Candles are now cut on the New York wall clock, so
  the candle spanning a clock change is 23 or 25 hours, like a broker's own charts. **This
  changes indicator values, signals and every metric on affected backtests** (anything built
  from finer candles on a 4h/1d timeframe — the default alignment — covering more than a few
  months); OANDA's own hourly-and-above candles were not affected. See [Backtesting
  methodology, point 6](docs/backtesting.md#signals-and-fills).
- **A stop closer than the spread now fills at the price you'd actually get**, not at the stop
  price itself, on the entry candle as well as later ones. Backtests where a tight stop sits
  inside the spread will show a bigger loss on those trades than before.
- **Shared-account replays keep a flat per-fill commission fixed when resizing a trade**,
  instead of scaling it with the new size; portfolios using the shared account with a flat
  commission will see a small change to its figures.
- **Candles with an impossible high or low are still repaired, but now counted** as
  `repaired_bars` in the metrics and flagged as a data-quality warning, where they previously
  passed silently.

## New in testing and validation

- Multiple-testing correction (Bonferroni and Benjamini-Hochberg) on the optimiser's
  combinations, and a neighbouring-settings (plateau/peak) check; a block-bootstrap 95% range
  for the Sharpe and Sortino ratios that allows for linked (overlapping) trades; results broken
  down by calendar year and by market condition; a Wilson range for the share of trades won;
  one **Robustness summary** on the Testing screen gathering the latest backtest, luck check
  and optimiser results. See [Statistical validation](docs/validation.md).
- A reproducibility **run record**: engine, library and indicator versions, the period as asked
  for, and the rules themselves, downloadable as JSON from any backtest. **Compare backtests**
  (`Strategies → Compare`) lines up two to four strategies' results side by side without
  marking a winner. **Combined risk** adds drawdown depth/length, volatility and concentration
  figures to the portfolio page. See [Backtesting methodology](docs/backtesting.md) and
  [Strategy development](docs/strategies.md).
- Automated data-quality notes for a possible stock split or dividend (an overnight move or
  pattern that looks like one), never adjusted automatically; provider and trading-hours facts
  on every run record. See [Data providers](docs/data-providers.md).

## Reliability and alerts

- **Alert delivery retries with a growing backoff** (up to about 30 minutes) before it's
  reported as failed, and Settings shows each channel's last delivered/last failed time; a
  retry after Altius crashes right after a channel accepted a message can occasionally send it
  twice (at-least-once delivery), rather than an alert being silently dropped within its retry
  budget.
- A crash partway through catching up several missed candles can no longer leave a trade or
  alert committed with the live checker's cursor still pointing before it (each candle's
  trade, event, equity point and cursor now land in one database transaction).
- Background jobs (the optimiser, luck checks) now survive a restart; a job still running at
  shutdown comes back as a plain "the server restarted while this was running" failure instead
  of vanishing.
- `tradealerts check-db` and an automatic integrity check at startup; `tradealerts
  verify-backup` and an automatic check right after a backup is made; `GET /api/health` for a
  short machine-readable status. See [Troubleshooting](docs/troubleshooting.md) and [API
  reference](docs/api.md).

## Security

- A sign-in throttle race that let parallel password guesses skip the wait is closed (check
  and count now happen together).
- **Self-hosted without a password now refuses API requests that arrive through a reverse
  proxy** (`X-Forwarded-For`/`X-Real-IP`/`Forwarded` present), rather than trusting them as
  local. See [Running on a server](docs/self-hosting.md).
- The hosted edition's alert address checks now refuse a webhook whose first response is a
  redirect to a different host, closing a redirect-based bypass of its address checks.
- `config.yaml`, the session secret and restore safety copies are written owner-only; CI now
  runs `pip-audit` and gitleaks on every push and weekly.

## Documentation and evidence

- [benchmarks/](benchmarks/README.md): a few example strategies on the deterministic Sample
  data market with their expected backtest metrics committed alongside them, rerun and checked
  with `tradealerts benchmark`.
- A [Limitations and known issues](docs/limitations.md) page, and a developer installation
  walkthrough in [docs/install.md](docs/install.md#for-developers).

Full detail, including the lower-severity items left open rather than fixed, is in
`Building Docs/audits/` (code, quantitative and security audits) and in each session's own
commit message.

# Changes in 0.3.1

## Maths fixes

- **RSI and ATR use exact Wilder smoothing.** The first value is the average of the first
  `period` values, then Wilder's recursion, matching TradingView and TA-Lib (checked against
  the StockCharts RSI table). Values differ slightly from 0.3.0 in the first few dozen candles.
- **Intraday Sharpe and Sortino suit session-limited markets.** Bars per year come from the
  candles actually seen per weekday (capped at 24 h), so hourly US stocks count about 7 bars
  a day, not 24. FX, crypto, daily and longer timeframes are unchanged.
- **Monte Carlo adds trade results when compounding is off.** With `risk: compound: false`
  the reshuffled paths add each trade's money result instead of compounding its % gain.
- Candles with an impossible high/low are still repaired, but now counted: `repaired_bars`
  in backtest metrics and a "repaired" data-quality warning.
- Spreads that are negative or infinite are rejected; crossed broker quotes count as 0.
- Strategy numbers must be finite (`.nan` / `.inf` rejected), and whole-number fields no
  longer accept `true`.

## Statistical robustness

- **Small-sample warning.** Backtests with few trades show a badge saying the figures rest on
  too few trades to trust (`sample_warning` in the metrics).
- **Sharpe 95% range.** The yearly Sharpe ratio comes with a 95% confidence interval
  (`sharpe_ci`), worked out from the spread of trade returns.
- **Slippage.** `costs.slippage` (price units) or `costs.slippage_pct` is charged on every
  market fill (entries, stops and rule exits, not limit targets). Trades get `slippage_cost`
  and the metrics `total_slippage`.

## Interface and secrets

- **The web interface is ES modules.** Each file in `static/` imports what it uses; the
  page loads `boot.js`, which imports the rest. Imports carry the page's `?v=` version, and
  `.js` is always served as `text/javascript` (some Windows setups said `text/plain`).
- **Passwords and tokens can live in the computer's password store.** With the optional
  `keychain` extra (bundled with the desktop app), saving Settings moves alert tokens,
  passwords, webhook addresses, ntfy topics and the OANDA token into Windows Credential
  Manager, the macOS Keychain or the Linux Secret Service, leaving `keychain:` references in
  `config.yaml`. Full backups carry the values; restoring stores them again. Settings says
  where they are kept. `tradealerts serve --no-keychain` keeps them in `config.yaml`.

## Getting started, stages and documentation

- **Sample data.** A built-in *Sample data* market of made-up prices generated on your
  computer: nothing to download, the same past every time, and new candles as time passes so a
  forward test works too. **Try it with sample data** (on the first screen and the dashboard,
  or `POST /api/strategies/sample`) saves a ready-to-test draft on it. Every result on it says
  it is not a real market.
- **Five stages.** Each strategy's page shows Research → Backtest → Validate → Paper trade →
  Monitor: which are done, when, whether the rules changed since a test, and the one next step
  with a link to it. The strategies list shows the next stage; the dashboard's getting-started
  checklist follows the same five steps. Facts only, no verdict on the results. Saved
  strategies record each backtest, split, luck check, optimiser run and settings comparison
  (`stages` in `/api/strategies` and `/api/strategies/{id}`).
- **Documentation** in `docs/`: installation, user guide, strategy development, YAML
  reference, backtesting methodology, statistical validation, data providers, API reference,
  security, troubleshooting and developer architecture. The README explains how the AGPL
  self-hosted edition and the paid hosted edition relate.

## Desktop app and testing

- **Port handshake.** The engine binds its own socket and prints `TRADEALERTS_PORT=<n>`; the
  desktop app reads it, so a busy port no longer leaves a blank window (`--port 0` works too).
- CI runs the tests on Python 3.11, 3.12 and 3.13, with a separate `ruff` lint job.

## Backups, sign-in and data checks

- **Shareable backups.** Settings, Backup now offers a full backup (private, includes tokens)
  and a shareable one (`/api/backup?redact=true`) that blanks passwords, API tokens, webhook
  addresses and ntfy topics, switches off the alerts that lost them, and leaves out the
  sign-in log. Its `config.yaml` starts with a comment saying what was removed.
- **Sign-in slow-down survives a restart.** Wrong-password counts and lockouts are kept in
  `state.db` (`login_attempts`), so restarting the server no longer resets them. Addresses
  that haven't tried for a day and aren't locked out are forgotten.
- **Train / test split for backtests.** Pick 60/40, 70/30 or 80/20 next to Run backtest
  (`split_pct` in `/api/backtest`). The first part is tested as usual (`metrics`), then the
  rest is traded again from a fresh balance (`oos_metrics`, `oos_equity`, `split_date`), with
  indicators already warmed up. The result shows both parts side by side.
- **More data checks.** The price data warning strip now also reports the total weekday
  candles missing when there are several gaps (`missing_bars`), and timestamps that go
  backwards or repeat (`disordered`).

# Changes in 0.2

## Self-hosted setup

`setup.sh` (Linux, macOS) and `setup.ps1` (Windows) install everything into their own folder
and offer: use it in the browser, keep it running in the background (systemd user service,
launchd agent or a Windows logon task), build the desktop app, or run it in Docker.

## Bugs fixed

**Rules and backtests**
- "None of these" / "not" conditions counted as true while an indicator was still warming
  up. For example "not RSI above 70" was true for the first 14 candles, and "not above the
  opening range high" was true every morning before the range had finished. Unknown now
  stays unknown, so those candles don't signal.
- A time window or weekday rule with a misspelt time zone was only caught when the rules
  ran; it's now caught when you save. Times like 24:30 are no longer accepted.
- RSI read 100 (overbought) on completely flat prices; it's now 50.
- Exits by "close after N candles" and at the end of a backtest were stamped at the start
  of the candle although they fill at its close, so charts showed them one candle early.

**Desktop app**
- Downloads (backups, the journal CSV, exported strategies) didn't save from the desktop
  window. They now go to the Downloads folder, with a notification.
- "View this week's report" tried to open a new window, which the desktop window blocks.
  It now opens in the same window; links to other websites open in the browser.
- Confirmation boxes (delete, stop a forward test, unsaved changes, restore) used the
  browser's built-in `confirm()`, which isn't reliable in desktop webviews. They're now the
  app's own dialogs, on every edition.

**Interface**
- Signing in again after a session expired started an extra status refresh each time.
- Delete and Duplicate failed silently on errors; the alerts switch showed alerts as on
  when saving had actually failed.
- The News calendar section was written but never shown in Settings. It's back.
- There was no way to sign out when a password is set. There's now a Sign out button.
- The weekly report went out at the server's time (UTC in Docker); it now has a time zone.
- You can upload CSV price files (Settings, Your price files, or Upload in a strategy's
  Market) instead of copying them into the data folder.

**Security and data**
- The ntfy access token was sent back to the browser on the Settings page; like the email
  password, it now stays on the server ("Saved. Leave blank to keep it").
- Alert addresses must now be web addresses (https:// or http://).
- Journal CSV export: notes starting with `=`, `+`, `-` or `@` could run as formulas when
  opened in a spreadsheet. They're now defused.
- Journal: the "exit before entry" check compared text instead of times.
- Restore copied the backup's database file over the live one. It now copies the rows into
  the current tables (older backups keep working, damaged or foreign files are refused),
  checks the zip's unpacked size, and keeps the last five safety copies instead of all.
- The live checker and the web page could write the news calendar at the same moment and
  corrupt it, and a failing calendar feed was retried on every check. Writes are now locked
  and atomic, and a failed fetch waits an hour.
- Two processes refreshing the same downloaded-price file used the same temporary name.
- The failed-password tracker could grow without limit under attack.

**Command line**
- `tradealerts new "Buy: the dip"` wrote a broken file (names with colons or quotes).
- `tradealerts run` didn't send news reminders or the weekly report, or update the news
  calendar, which `tradealerts serve` does. It now does all of them.
- `tradealerts reset` created an empty database when there wasn't one.

**Builds**
- The desktop workflow used GitHub's `macos-13` runner, retired in December 2025. Intel Mac
  builds now use `macos-15-intel`.

## Clean-up

- `web.py` (2,000 lines) is now `web/`: the app factory, shared helpers, and one module of
  routes per area (`routes/strategies.py`, `runs.py`, `testing.py`, `markets.py`, ...).
- `app.js` (4,500 lines) is now 15 scripts by area (`util.js`, `editor.js`, `dashboard.js`,
  ...), still with no build step.
- Every request works on a *workspace* (one person's settings, strategies and data), which
  is what lets the same engine serve one person or many.
- Loading settings no longer changes global state (the news folder is set explicitly).
- The testing tools (`compute.py`) are plain functions, so the hosted service can run them
  in worker processes.
- 17 new tests (191 in total).
