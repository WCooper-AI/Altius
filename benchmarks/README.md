# Benchmarks

A few example strategies, backtested over a fixed period on the built-in **Sample data**
market (made-up prices, not a real market — see [docs/data-providers.md](../docs/data-providers.md#sample-data)),
with their known-good metrics committed next to them. Because Sample data is generated once
from a fixed seed and start date, the candles between any two calendar dates in its past never
change, so the same strategy over the same period gives the same trades and the same metrics
every time, on every machine.

```
strategies/<name>.yaml    one strategy file per benchmark
periods.json               {"<name>": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}}, one fixed period each
expected/<name>.json       that benchmark's committed metrics
```

Rerun every benchmark and compare with the committed metrics:

```bash
tradealerts benchmark          # from the repository root
```

A mismatch means the simulator (or the statistics built on it) changed, not that the market
data did — the data can't change. After a deliberate, reviewed change to the simulator,
regenerate the committed metrics with:

```bash
tradealerts benchmark --write
```

These are not trading recommendations: they exist to catch an unintended change to the
engine's numbers, not to show what works. See [engine/README.md](../engine/README.md#commands)
for the rest of the command line.
