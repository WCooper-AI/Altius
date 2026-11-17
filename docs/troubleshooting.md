# Troubleshooting

Messages in the app say what went wrong in plain words. This page adds what to do about the
common ones. Self-hosted, the server's log (the terminal window, the service's log, or
`docker compose logs`) has the detail; `log_level: debug` in `config.yaml` adds more.

## Installing and starting

| Problem | What to do |
|---|---|
| `setup.ps1` will not run on Windows | Right-click it and choose *Run with PowerShell*. If PowerShell refuses scripts, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once in a PowerShell window. |
| "Python 3.11 or newer" | Install it from python.org (Windows: tick *Add to PATH*) and run the setup script again. |
| The page does not open at `localhost:8321` | Something else may be using the port: start with `tradealerts serve --port 8322` (or `--port 0` for any free port, printed at start). The desktop app picks a free port by itself. |
| "Refusing to listen on … without a password" | You asked for an address other than localhost. Set `TRADEALERTS_PASSWORD`, or pass `--no-auth` only if a reverse proxy already signs people in. See [Security](security.md). |
| The desktop app build fails | It needs Rust and Node.js; the setup script prints how to get them. The Linux `.deb` and `.AppImage` on the releases page need no build. |
| Signed out after a restart | Normal after a password change (it signs everyone out). Otherwise the secret file under the data folder was removed. |

## Price data

| Message | Meaning and what to do |
|---|---|
| "No price data in that period" | The source has nothing for those dates. Yahoo keeps about 2 years of hourly candles and 60 days of finer ones; pick a bigger candle size or a shorter period, or switch to Dukascopy in Settings. |
| "You asked for the last N years, but this price data only goes back to …" | Not an error: the results cover the shorter period shown. |
| "Couldn't reach Dukascopy" | No connection, or Dukascopy is blocking automated downloads for a while. Try again later, use Yahoo or OANDA, or load a CSV export. The first download of a market can take a few minutes; later only new candles are fetched. |
| "… isn't available from OANDA here" | Some markets depend on your region, and stocks are never on OANDA. Switch the price source for that strategy's market. |
| "CSV file not found" / "needs a time column" | Self-hosted, the path must be inside the data folder. The file needs a `timestamp`, `time`, `date` or `datetime` column and `open, high, low, close`. See [Data providers](data-providers.md). |
| A data-quality warning (gap, jump, repaired, stale…) | A fact about the candles, not a fault in your rules: see the table in [Data providers](data-providers.md). Futures symbols on Yahoo jump at each contract roll. |
| Forward test says "waiting for its market to open" | Normal outside trading hours; the dashboard shows when it reopens. |
| Results on Sample data | Made-up prices. They are for trying the tool and say nothing about a real market. |

## Testing

| Message | Meaning and what to do |
|---|---|
| "Still to fill in: …" / "Still to choose: …" | Every setting starts blank; fill the ones listed before testing. |
| No trades in the backtest | The result says which condition never matched. Loosen it, change the candle size, or check the direction. |
| "Too few trades" sample warning | The figures are about a small number of trades; the ranges are wide. Test a longer period or a smaller candle size. |
| "You already have an optimiser run going" / "The server is busy" | One heavy job at a time per workspace. Stop it from the Optimiser tab or wait. |
| The optimiser refuses the grid | At most 3 settings and 2,000 combinations (200 on the hosted edition). |
| A luck check takes long | It runs up to 200 extra backtests; a minute is normal on a long period. |

## Alerts

| Problem | What to do |
|---|---|
| No alerts arrive | Altius must be running: a sleeping laptop sends nothing. Press *Send test alert* in Settings; the Activity page lists failed alerts with the reason. |
| ntfy test works but strategy alerts do not | The strategy must be *Alerts on* (not Draft or Paused), and its market must be open. Check the dashboard for "needs attention". |
| Email alerts fail | The SMTP host, port and security (STARTTLS or SSL) must match your provider; many need an app password rather than the account password. |
| Telegram sends nothing | Start a chat with your bot first; the chat ID comes from @userinfobot. |
| Hosted edition: "only public internet addresses" | Webhooks and SMTP servers on the hosted edition must be reachable from the internet. |

## Using the API

| Message | Meaning |
|---|---|
| `403 Request blocked: missing X-Requested-With header` | Add `X-Requested-With: tradealerts` to every request that is not a GET. |
| `401` | Sign in first (`POST /api/login`) and send the cookie. |
| `409 code: "running_test"` | The strategy has a running forward test; resend with `on_running` set to `restart`, `keep` or `stop`. |
| `421 Unknown host` | The `Host` header does not match an address the server was started for. |

## Getting your data back

Everything is in your `TradingAlerts` folder (self-hosted) or in a backup zip from
*Settings → Backup*. Strategy files are plain YAML; the database is SQLite; the journal
exports to CSV. A deleted strategy's file is kept in `data/deleted-strategies`.
