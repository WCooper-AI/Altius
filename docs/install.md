# Installation

Altius comes in two editions built on the same engine: **self-hosted** (free, this
repository, runs on your own computer or server) and **hosted** (a paid service, nothing to
install). This page is about the self-hosted edition.

## Requirements

- Python 3.11 or newer on Linux, macOS or Windows. No administrator rights are needed for a
  normal install.
- An internet connection for price data and alerts. Backtests on the built-in **Sample data**
  market work offline.

## Self-hosted, from a release

1. Download and unzip the latest release.
2. Run the setup script for your platform:
   - Linux / macOS: `./setup.sh`
   - Windows: right-click `setup.ps1` and choose *Run with PowerShell*
3. Choose how to run it:

| Choice | What you get |
|---|---|
| Use it in your browser | Starts now at `http://localhost:8321`, on this computer only. Next time, run `trading-alerts` (Windows: the *Start Trading Alerts* shortcut). |
| Keep it running in the background | Starts when you log in, so forward tests and alerts carry on with no window open. |
| Build the desktop app | A normal app with its own window and tray icon. Needs Rust and Node.js (the script says how to get them); the first build takes about 10 minutes. Linux `.deb` and `.AppImage` builds are attached to each release. |
| Run it in Docker | For a home server or VPS that is always on. The script sets a password for you. |

Everything installs into its own folder. Your strategies, settings and results live in a
`TradingAlerts` folder in your home folder (the desktop app keeps its own; move between them
with *Settings → Backup*). To uninstall, delete the install folder. To upgrade, download the
new release and run its setup script again; the data folder is separate and stays as it is.

Forward tests and alerts only run while Altius is running. A computer that sleeps cannot send
alerts, so for alerts around the clock use the background service on an always-on machine, or
Docker on a server.

Full details, including the service and the Docker files: [selfhost/README.md](../selfhost/README.md).

## On a server

`./setup.sh --docker` (or `docker compose up -d --build` after copying `.env.example` to
`.env` and setting `TRADEALERTS_PASSWORD`) runs it in one container with everything in
`./workspace`. The port is published on `127.0.0.1` only: put a reverse proxy with HTTPS in
front of it. [Running on a server](self-hosting.md) has Caddy and nginx examples, firewall
rules and a sign-in checklist; [Security](security.md) covers what the password protects and
what the server refuses to do without one.

## For developers

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "engine[dev,yahoo]"
pytest engine/tests
tradealerts serve        # http://localhost:8321
```

| Extra | Adds | Needed for |
|---|---|---|
| `yahoo` | yfinance | Yahoo Finance as a price source |
| `stocks` | exchange_calendars | Exact exchange holidays for stocks (without it, a plain Mon–Fri 09:30–16:00 New York timetable is used) |
| `parquet` | pyarrow | A Parquet price cache instead of CSV files (faster to load; the cache keeps revisions) |
| `keychain` | keyring | Passwords and tokens in the computer's password store instead of `config.yaml` (bundled in the desktop app) |
| `dev` | pytest, pytest-cov, ruff, hypothesis, httpx | Running the tests and the linter |

Dukascopy (the default price source) and OANDA need no extra. The command line is described in
[engine/README.md](../engine/README.md#commands); `tradealerts init [dir]` creates a
`config.yaml`, the `strategies/`, `data/` and `reports/` folders, and example strategies in
`examples/`.

## Hosted edition

The hosted edition runs the same engine on a managed, always-on server, with an account and a
subscription. There is nothing to install: sign up in a browser, or use the thin desktop client.
Your strategies and data are stored on the hosted server rather than on your machine. Its
account, billing and multi-customer code is not part of this repository (see
[Licence and editions](../README.md#licence-and-editions)).
