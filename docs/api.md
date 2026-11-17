# API reference

The web interface is a plain client of a JSON API, and anything it does you can do with
`curl` or a script. The API is the same in both editions.

## Basics

- Base URL: the server, e.g. `http://localhost:8321`. Paths start with `/api/`; `/api/v1/` is
  an alias of the same API, and every response carries an `API-Version: 1` header.
- **Sign in.** With a password set, `POST /api/login` with `{"password": "…"}` sets a signed,
  HttpOnly session cookie; `POST /api/logout` clears it; `GET /api/session` says whether you are
  signed in. Without a password (the default on localhost) every request is accepted. The
  hosted edition signs in with an account instead.
- **Header.** Every request other than `GET`/`HEAD` must send `X-Requested-With: tradealerts`;
  without it the server answers `403`. This stops a web page on another site from using your
  session.
- **Errors** are JSON with a plain-text `detail`: `{"detail": "The test period must be between
  1 and 20 years"}`. `422` is a problem in the request, `404` a missing strategy or run, `409`
  something that needs a choice (for example editing a strategy with a running forward test:
  `code: "running_test"`, resend with `on_running: restart | keep | stop`), `429` another
  heavy job is still running, `401` not signed in.
- **Strategy ids** are the file name without `.yaml`, made from the name (`my-pullback-rule`).
- **Strategy data** in request bodies is the strategy file as JSON (see the
  [YAML reference](yaml-reference.md)); the testing routes take the data as it is on screen,
  saved or not, plus an optional `strategy_id` so the strategy's stage strip can record the
  test.

## Example

```bash
curl -s http://localhost:8321/api/backtest \
  -H 'Content-Type: application/json' -H 'X-Requested-With: tradealerts' \
  -d '{"data": {...strategy...}, "years": 2, "split_pct": 70}'
```

The response has `metrics`, `trades`, `equity`, `notes`, `quality` and `meta` (the run
record). Periods are `years` (1–20) or `start`/`end` dates (`2025-01-31`).

## Background jobs

The optimiser runs in the background: `POST /api/optimise` returns `{"job": id,
"combinations": n}`. `GET /api/jobs/{id}` gives `status` (`queued`, `running`, `completed`,
`failed`, `cancelled`), `done`/`total`, times, and `result` when completed or `error` when
failed. `POST /api/jobs/{id}/cancel` stops it; `GET /api/jobs` lists recent jobs. One heavy
job runs per workspace at a time.

## Endpoints

### Reference data

| Method and path | Purpose |
|---|---|
| `GET /api/meta` | Indicators with parameters, price fields, operators, candle sizes, time zones, currencies, priorities, this workspace's limits |
| `GET /api/recipes` | The guided ideas, the built-in markets, and each market's instrument details |
| `GET /api/markets` | The built-in markets and instrument details |
| `GET /api/examples`, `GET /api/examples/{name}` | The example strategy files |

### Strategies

| Method and path | Purpose |
|---|---|
| `GET /api/strategies` | All strategies with status, running forward test, and `stages` (the five-stage block) |
| `POST /api/strategies` | Save a new strategy: `{"data": …}` → `{"id": …}` |
| `POST /api/strategies/sample` | Save a ready-to-test draft on the Sample data market → `{"id": …}` |
| `GET /api/strategies/{id}` | One strategy: `data`, `error`, `run`, `version`, `stages` |
| `PUT /api/strategies/{id}` | Save changes: `{"data": …, "on_running": …}` |
| `DELETE /api/strategies/{id}` | Delete (the file is kept in `data/deleted-strategies`) |
| `POST /api/strategies/{id}/reset` | Clear the strategy's live state |
| `GET /api/strategies/{id}/export`, `POST /api/strategies/import` | The rules as a file, and back (imports arrive as drafts) |
| `GET /api/strategies/{id}/versions`, `…/versions/{n}`, `POST …/versions/{n}/restore` | Saved versions |
| `POST /api/validate` | Check strategy data without saving |
| `POST /api/yaml`, `POST /api/yaml/parse` | Strategy data to YAML text and back |
| `POST /api/recipes/preview`, `POST /api/recipes/build` | The guided setup's sentence and the strategy it builds |

### Testing

| Method and path | Purpose |
|---|---|
| `POST /api/backtest` | `{data, years | start/end, split_pct?, strategy_id?}` |
| `POST /api/signals/count` | How often the entry rule fired on past data |
| `POST /api/sweep` | One setting over a list of values: `{data, setting: {kind: "knob", key} | {kind: "path", path}, values, …}` |
| `POST /api/optimise` | Up to three settings: `{data, settings: [{setting, values}], score, min_trades, holdout_pct, walk_forward, …}` → job |
| `POST /api/luck` | The luck check: `{data, trials?, …}` |
| `POST /api/portfolio` | Several strategies on one balance, with weights and shared-account caps |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel` | Background jobs |

### Forward tests and status

| Method and path | Purpose |
|---|---|
| `POST /api/strategies/{id}/forward-test/start`, `…/stop` | Start or stop a forward test |
| `GET /api/dashboard` | Running and finished forward tests, recent events |
| `GET /api/runs/{run_id}`, `DELETE /api/runs/{run_id}` | One forward test with its trades and equity |
| `POST /api/runs/{run_id}/backtest-range` | The live-versus-backtest usual range |
| `GET /api/status`, `POST /api/run-now` | The live checker's state; check now |
| `GET /api/activity`, `POST /api/activity/seen`, `GET /api/paper` | The activity timeline, mark as seen, paper trades |
| `GET /api/health` | A short machine-readable check for monitoring: database ok, when the checker last ran, job counts, last backup |

### Markets, alerts, news, journal

| Method and path | Purpose |
|---|---|
| `GET /api/candles` | Candles for a market and candle size, with data-quality findings |
| `GET /api/market-stats` | Typical moves per market |
| `GET/POST /api/price-alerts`, `PUT/DELETE /api/price-alerts/{id}` | Price alerts (typed bodies; `422` lists the field) |
| `GET /api/uploads`, `POST /api/uploads/{name}`, `DELETE /api/uploads/{name}` | Your CSV price files |
| `GET /api/news`, `GET /api/news/events`, `PUT /api/news/events/{key}`, `POST /api/news/update` | The economic calendar and your notes |
| `GET /api/journal`, `GET /api/journal.csv`, `POST /api/journal`, `PUT/DELETE /api/journal/{id}`, `GET /api/paper-trades` | The trade journal |

### Settings and backups

| Method and path | Purpose |
|---|---|
| `GET /api/settings`, `PUT /api/settings`, `PUT /api/appearance` | Settings (secrets are never sent back; blank keeps the saved one) |
| `POST /api/test-alert`, `POST /api/data/test` | Send a test alert; test the price source |
| `GET /api/report`, `GET /api/report.html`, `POST /api/report/send` | The weekly report |
| `GET /api/backup?redact=true`, `POST /api/restore` | Backup zip (optionally with secrets blanked) and restore |
| `GET /api/session`, `POST /api/login`, `POST /api/logout` | Session |

The routes are defined in `engine/src/tradealerts/web/routes/`, one module per area. The
automatic `/docs` and `/openapi.json` pages are switched off, so the module files are the
reference for request and response shapes.
