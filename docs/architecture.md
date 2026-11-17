# Developer architecture

One Python package, `tradealerts` (in `engine/src/`), is the whole engine: the rule language,
the simulator, the testing tools, the live checker, the alert channels, the SQLite store, the
JSON API and the plain-JavaScript interface. The self-hosted edition runs it for one person;
the hosted edition (a separate private repository) wraps it with accounts, billing and
per-customer limits.

```
            +------------------- engine (tradealerts) -------------------+
            | rules -> strategy -> Simulator -> backtest / analysis       |
            | data providers (Dukascopy, Yahoo, OANDA, CSV, sample)       |
            | runner -> notify        web/ routes per area  static/ UI    |
            +-----------------------------+------------------------------+
                        |                                   |
          self-hosted edition                      hosted edition (private repo)
  setup.sh/ps1, service, Docker,           accounts, Stripe, read-only gating,
  Tauri app with the engine inside         SSRF guard, admin CLI, thin desktop client
```

## Modules

| Module | Role |
|---|---|
| `rules.py`, `indicators.py`, `strategy.py` | The rule tree (`all` / `any` / `not` around `{left, op, right}`; operands are a price field, a number or an indicator with `shift` / `scale` / `add`; plus `time_between` and `weekdays`), the indicators, and strategy files with validation and migrations. `Strategy.warmup_candles` is the history an indicator needs to settle. |
| `simulator.py` | One `Simulator` for backtests and forward tests: next-open fills, bid/ask, stops and targets with finer-candle ordering, spread, slippage, funding, borrow, commission, loss limits, margin and close-out, currency conversion. `InvariantError` when the books do not balance. |
| `backtest.py`, `measures.py`, `compute.py` | `compute_metrics` and the extra measures (CAGR, Calmar, expected shortfall, drawdown length, by weekday and session); `compute` wraps the testing tools as plain functions for the job runner. |
| `analysis.py`, `stats.py` | Sweep, optimiser grid with hold-out and anchored walk-forward, luck check, Monte Carlo, live-vs-backtest ranges, portfolio and shared account; the statistics behind them (Wilson interval, block bootstrap, deflated Sharpe, probability of backtest overfitting, market states). |
| `invariants.py`, `limits.py`, `instruments.py`, `fxrates.py`, `markethours.py` | Accounting checks on results; loss-limit bookkeeping; the typed catalogue of built-in markets; exchange rates; trading hours and exchange calendars. |
| `data.py`, `cache.py`, `dataquality.py`, `sample.py` | Providers (`DukascopyProvider`, `YahooProvider`, `OandaProvider`, `CSVProvider`, `SampleProvider`, and `MarketProvider` for `provider: auto`), the Parquet/CSV price cache, data-quality findings and fingerprints, the generated sample series. |
| `stages.py` | The five-stage block (Research → Backtest → Validate → Paper trade → Monitor) computed from the store's test records, the active run and the alert status. |
| `runner.py`, `watch.py`, `notify.py`, `news.py`, `weekly.py`, `pricealerts.py` | The live checker and what it sends: signals, paper trades, data and drawdown warnings, price alerts, news reminders, the weekly report; the channels. |
| `workspace.py`, `config.py`, `state.py`, `versions.py`, `keychain.py`, `jobs.py` | One person's settings, folders and SQLite store; strategy versions; secrets in the password store; background jobs with a lifecycle (`queued`, `running`, `completed`, `failed`, `cancelled`). |
| `contracts.py` | `Protocol`/`TypedDict` descriptions of the boundaries above, as they actually are (see "Formal contracts" below); no behaviour. |
| `web/` | The FastAPI app (`app.py`: security headers, CSRF header check, `/api/v1` alias, versioned static files), `auth.py`, Pydantic models, and `routes/` with one module per area. |
| `static/` | The interface as ES modules with no build step: `boot.js` is the entry; `util.js` imports nothing; `guided.js`, `editor.js`, `describe.js`, `testing.js`, `dashboard.js`, `markets.js`, `widgets.js` and the rest each own one area. |
| `cli.py` | `tradealerts init / new / validate / backtest / check-data / run / serve / status / reset / test-alert / sample-data`. |

## Request flow

A page calls the JSON API; every request resolves a `Workspace` (config, folders, store).
Testing routes validate the strategy data, load prices through `web/prices.load_period`
(provider, cache, period, exchange rates), run the plain function from `compute` through the
job runner (`InlineJobs` self-hosted, `PoolJobs` hosted), stamp the result with the run record,
and leave a test record for the strategy's stage strip. The runner polls on a timer, feeds
closed candles to the same `Simulator`, and writes events and paper trades to the store.

## Formal contracts

`contracts.py` names the shapes at the boundaries below as they already are (a `Protocol`
for anything with more than one implementation, a `TypedDict` for a plain dict that crosses
a boundary); it does not change behaviour or add an abstraction layer. `tests/test_b7_contracts.py`
checks every real implementation against its Protocol, that a real trade dict and backtest
metrics dict have exactly the documented keys, that a couple of key API responses carry the
documented keys, and that `simulator`/`backtest`/`analysis`/`stats`/`measures` never import
FastAPI, Starlette, sqlite3, httpx or requests.

| Boundary | Protocol / TypedDict | Implementations |
|---|---|---|
| Market data | `PriceProvider` | `CSVProvider`, `YahooProvider`, `OandaProvider`, `DukascopyProvider`, `SampleProvider`, `MarketProvider` (`data.py`) |
| Strategy definition | `Strategy`, `Market`, `Risk` (dataclasses, not reinvented as a Protocol: there is one canonical representation) | `strategy.py` |
| Simulation events | `TradeRecord` | `Simulator._close`'s `closed` event (`simulator.py`); persisted and read back by `StateStorage.add_trade`/`trades` |
| Backtest results | `BacktestMetrics`, `BacktestAPIResponse` | `backtest.run_backtest`/`compute_metrics`; `POST /api/backtest` (`web/routes/testing.py`, built by `compute.backtest`) |
| Notifications | `Notifier` | `ConsoleNotifier`, `NtfyNotifier`, `DiscordNotifier`, `TelegramNotifier`, `SlackNotifier`, `WebhookNotifier`, `EmailNotifier` (`notify.py`) |
| Background jobs | `JobRunner`, `JobStatus`, `JobRecord` | `InlineJobs`, `PoolJobs` (`jobs.py`); persisted rows and `GET /api/jobs` |
| Persistence | `StateStorage`, `RunRecord`, `DeliveryRecord` | `StateStore` (`state.py`); `POST /api/strategies/{id}/forward-test/start` returns a `RunRecord` as-is |
| Operational status | `HealthStatus` | `GET /api/health` (`web/routes/status.py`) |

## Storage

SQLite tables: `strategy_state`, `runs` (with a snapshot of the rules they ran on),
`run_equity`, `paper_trades`, `events`, `price_alerts`, `news_notes`, `journal`,
`strategy_versions` (fifty per strategy), `strategy_tests` (latest test per kind, for the
stages), `settings_kv`, `login_attempts`. Strategies are YAML files; settings are
`config.yaml`; prices are cached under `data/cache`.

## The two editions

Hosted workspaces load with `trusted=False` (no `${ENV}` expansion, no folder overrides),
get one SQLite database per customer, one heavy job at a time, alert targets limited to
public addresses, and read-only mode when a subscription lapses. The engine never knows about
accounts or billing; the hosted repository adds them around it and pins this repository as a
git submodule.

## Tests and CI

`pytest engine/tests` (hand-worked reference scenarios and property tests for the simulator,
route tests through FastAPI's test client, a node test that loads the interface modules);
`npm run lint:js` for the interface; `scripts/docker-smoke.sh` for the container. GitHub
Actions runs the suite on Linux (3.11–3.13), Windows and macOS, with coverage, CodeQL,
dependency review, Dependabot and an SBOM on releases.
