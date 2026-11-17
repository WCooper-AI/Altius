# Altius documentation

Altius is a tool for testing and monitoring trading rules you write yourself. It backtests
them on past prices, checks how much of a result could be chance, follows them on live
prices without risking money, and sends you an alert when they fire. It never places trades
and it never recommends them.

The work goes through five stages, and the app shows where each strategy is:

**Research → Backtest → Validate → Paper trade → Monitor**

| Section | What it covers |
|---|---|
| [Installation](install.md) | Self-hosted setup (browser, background service, desktop app, Docker), developer install, extras, the hosted edition |
| [Running on a server](self-hosting.md) | HTTPS with Caddy or nginx, firewall, sign-in hardening checklist, secrets and backups on a VPS |
| [User guide](user-guide.md) | The pages of the app, the five stages, alerts, sample data, backups |
| [Strategy development](strategies.md) | Guided setup, the rule builder, markets and instruments, versions, sharing, testing strategies together |
| [YAML reference](yaml-reference.md) | Every key in a strategy file and in `config.yaml` |
| [Backtesting methodology](backtesting.md) | How fills, costs, sizing, loss limits, margin and currencies are simulated; the run record; the metrics |
| [Statistical validation](validation.md) | Train/test split, luck check, optimiser checks, live-vs-backtest ranges, and how to read them |
| [Data providers](data-providers.md) | Dukascopy, Yahoo Finance, OANDA, CSV files, sample data, caching, data-quality checks, exchange rates |
| [API reference](api.md) | The JSON API the interface uses: sign-in, headers, versions, errors, background jobs, endpoints |
| [Security](security.md) | Passwords and sessions, browser protections, secrets, backups, the hosted edition's limits, reporting a problem |
| [Troubleshooting](troubleshooting.md) | Common problems and what they mean |
| [Developer architecture](architecture.md) | Modules, request flow, storage, jobs, the two editions, tests and CI |
| [Limitations and known issues](limitations.md) | What the tool doesn't do, what it approximates, and low-severity items the project's own audits left open |

Not financial advice: see [engine/DISCLAIMER.md](../engine/DISCLAIMER.md). Licence: GNU AGPL-3.0,
see the [README](../README.md#licence-and-editions).
