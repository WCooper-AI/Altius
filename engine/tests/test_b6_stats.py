"""Session B6: multiple-testing corrections, linked-returns Sharpe/Sortino ranges, results by
calendar year, parameter stability, selection-bias wording, paper-trading note and the
robustness summary."""
import copy
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradealerts import analysis, compute, measures, stats, weekly
from tradealerts.sample import synthetic_bars
from tradealerts.state import StateStore

from .test_web import CSV_STRATEGY

STATIC = Path(__file__).resolve().parents[1] / "src" / "tradealerts" / "static"
STUB = Path(__file__).parent / "js" / "browser_stub.mjs"


def variant(rsi_level, atr):
    d = copy.deepcopy(CSV_STRATEGY)
    d["entry"]["all"][0]["right"] = rsi_level
    d["exit"]["stop"]["atr"] = atr
    return d


@pytest.fixture(scope="module")
def bars():
    return synthetic_bars(days=200, freq="1h", seed=4)


@pytest.fixture(scope="module")
def optimised(bars):
    grid = [((lv, a), variant(lv, a)) for lv in (30, 35, 40) for a in (1.5, 2.0)]
    return analysis.optimise(None, grid, bars, None, None, score="return", min_trades=5, walk_forward_folds=3)


# ------------------------------------------------------------------ t-test and corrections


@pytest.mark.parametrize("t,df,p", [(1.812461, 10, 0.05), (2.228139, 10, 0.025), (12.7062, 1, 0.025),
                                    (2.570582, 5, 0.025), (1.644854, 10 ** 7, 0.05)])
def test_t_tail_matches_tables(t, df, p):
    assert stats.t_upper(t, df) == pytest.approx(p, abs=2e-6)
    assert stats.t_upper(-t, df) == pytest.approx(1 - p, abs=2e-6)
    assert stats.t_upper(0.0, df) == pytest.approx(0.5)


def test_incomplete_beta_known_value_and_symmetry():
    assert stats.betainc(2, 3, 0.5) == pytest.approx(0.6875)
    for a, b, x in [(0.5, 4.0, 0.3), (6.0, 0.5, 0.9), (30.0, 0.5, 0.97)]:
        assert stats.betainc(a, b, x) == pytest.approx(1 - stats.betainc(b, a, 1 - x), abs=1e-12)


def test_mean_test_p_value():
    r = np.array([0.01, -0.005, 0.02, 0.003, -0.01, 0.015])
    t = r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))
    assert stats.mean_above_zero_p(r) == pytest.approx(stats.t_upper(t, len(r) - 1))
    assert stats.mean_above_zero_p(np.array([0.01])) is None
    assert stats.mean_above_zero_p(np.full(5, 0.01)) is None


def test_bonferroni_and_benjamini_hochberg_by_hand():
    p = np.array([0.01, 0.04, 0.03, 0.005])
    assert stats.bonferroni(p) == pytest.approx([0.04, 0.16, 0.12, 0.02])
    # sorted 0.005, 0.01, 0.03, 0.04 -> x4/1, x4/2, x4/3, x4/4 = 0.02, 0.02, 0.04, 0.04
    assert stats.benjamini_hochberg(p) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert stats.bonferroni(np.array([0.6, 0.9])).max() == 1.0
    assert len(stats.benjamini_hochberg(np.array([]))) == 0


def test_multiple_testing_counts_before_and_after_corrections():
    rng = np.random.default_rng(1)
    noise = [rng.normal(0, 0.01, 40) for _ in range(19)]
    strong = rng.normal(0.01, 0.01, 40)
    out = stats.multiple_testing(noise + [strong, np.array([0.02])], chosen=19)
    assert out["tests"] == 21 and out["untestable"] == 1
    assert out["raw"] >= out["bh"] >= out["bonferroni"] >= 1
    c = out["chosen"]
    assert c["trades"] == 40 and c["p"] < 1e-4
    assert c["bonferroni"] == pytest.approx(min(1.0, c["p"] * 21))
    assert c["p"] <= c["bh"] <= c["bonferroni"]
    assert stats.multiple_testing([], None) is None
    assert stats.multiple_testing([np.array([0.01])], 0)["chosen"]["p"] is None


# ------------------------------------------------------------------ linked-returns ranges


def test_ratio_ranges_match_a_direct_block_bootstrap():
    rng = np.random.default_rng(3)
    r = rng.normal(0.0005, 0.01, 103)
    b, runs, scale = 10, 50, math.sqrt(252)
    out = stats.ratio_ranges(r, scale, b, runs=runs, rng=np.random.default_rng(7))
    # the same draws, resampled the slow way
    g = np.random.default_rng(7)
    starts = g.integers(0, len(r), (runs, 10))
    rest = g.integers(0, len(r), runs)
    sh, so = [], []
    for k in range(runs):
        idx = np.concatenate([(np.arange(s, s + b) % len(r)) for s in starts[k]] + [np.arange(rest[k], rest[k] + 3) % len(r)])
        x = r[idx]
        sh.append(x.mean() / x.std(ddof=1) * scale)
        so.append(x.mean() / math.sqrt((np.minimum(x, 0) ** 2).mean()) * scale)
    assert out["sharpe"] == pytest.approx(list(np.percentile(sh, [2.5, 97.5])))
    assert out["sortino"] == pytest.approx(list(np.percentile(so, [2.5, 97.5])))
    assert out["block"] == b and out["candles"] == 103


def test_linked_returns_widen_the_range_when_returns_run_together():
    rng = np.random.default_rng(5)
    shocks = rng.normal(0.0003, 0.01, 4000)
    r = np.convolve(shocks, np.ones(20) / 20, mode="same")  # strongly linked neighbours
    one = stats.ratio_ranges(r, 1.0, 1)
    linked = stats.ratio_ranges(r, 1.0, 60)
    assert (linked["sharpe"][1] - linked["sharpe"][0]) > 2 * (one["sharpe"][1] - one["sharpe"][0])
    assert stats.ratio_ranges(r[:20], 1.0) is None
    assert stats.ratio_ranges(np.zeros(100), 1.0) is None


def test_backtest_reports_linked_ranges_regimes_and_years(bars):
    res = compute.backtest(CSV_STRATEGY, bars, None, None, [])
    m = res["metrics"]
    rr = m["ratio_ranges"]
    assert rr["block"] >= max(stats.block_size(rr["candles"]), math.ceil(m["avg_bars_held"]))
    assert rr["sharpe"][0] < m["sharpe"] < rr["sharpe"][1]
    assert rr["sortino"][0] < m["sortino"] < rr["sortino"][1]
    assert rr["overlapping_trades"] == m["overlapping_trades"] == 0
    assert res["regimes"]["rows"] and sum(r["trades"] for r in res["regimes"]["rows"] if r["split"] == "activity") <= m["trades"]
    years = m["by_year"]
    assert [y["year"] for y in years] == [2024] and sum(y["trades"] for y in years) == m["trades"]
    assert years[0]["change_pct"] == pytest.approx(m["total_return_pct"])
    lo, hi = stats.wilson(round(m["win_rate_pct"] * m["trades"] / 100), m["trades"])
    assert m["win_rate_range"] == pytest.approx([lo * 100, hi * 100])


def test_split_backtest_counts_market_days_in_the_first_part_only(bars):
    whole = compute.backtest(CSV_STRATEGY, bars, None, None, [])
    split = compute.backtest(CSV_STRATEGY, bars, None, None, [], 50)
    assert split["oos_metrics"] and split["regimes"]
    assert split["regimes"]["rows"] != whole["regimes"]["rows"]


def test_no_trades_means_no_ranges(bars):
    never = copy.deepcopy(CSV_STRATEGY)
    never["entry"] = {"all": [{"left": "close", "op": "<", "right": -1}]}
    res = compute.backtest(never, bars, None, None, [])
    assert res["metrics"]["ratio_ranges"] is None and res["regimes"] is None
    assert res["metrics"]["win_rate_range"] is None and res["metrics"]["overlapping_trades"] == 0


# ------------------------------------------------------------------ calendar years, overlap


def test_by_year_splits_balance_drops_and_trades():
    idx = pd.date_range("2023-12-29", "2024-01-03", freq="D", tz="UTC")
    eq = pd.Series([1000, 1100, 990, 1000, 1200, 1080], index=idx, dtype=float)
    trades = pd.DataFrame({"exit_time": [idx[1], idx[4], idx[5]], "pnl": [100.0, 200.0, -120.0], "r_multiple": [1.0, 2.0, -1.0]})
    rows = measures.by_year(trades, eq)
    assert [r["year"] for r in rows] == [2023, 2024]
    a, b = rows
    assert a["change_pct"] == pytest.approx(-1.0) and a["max_drawdown_pct"] == pytest.approx(-10.0)
    assert (a["trades"], a["pnl"], a["won_pct"]) == (1, 100.0, 100.0)
    assert b["change_pct"] == pytest.approx(1080 / 990 * 100 - 100)
    assert b["max_drawdown_pct"] == pytest.approx(-10.0)  # 1200 -> 1080
    assert (b["trades"], b["pnl"], b["avg_r"]) == (2, 80.0, 0.5)
    assert measures.by_year(trades.iloc[0:0], eq.iloc[:1]) == []


def test_overlapping_trades_and_win_rate_range():
    t = pd.to_datetime(["2024-01-01 00:00", "2024-01-01 05:00", "2024-01-01 03:00", "2024-01-01 06:00",
                        "2024-01-01 08:00", "2024-01-01 09:00"], utc=True)
    trades = pd.DataFrame({"entry_time": [t[0], t[2], t[4]], "exit_time": [t[1], t[3], t[5]], "pnl": [1.0, -1.0, 2.0]})
    assert measures.overlapping_trades(trades) == 1  # the second opened before the first closed
    lo, hi = stats.wilson(2, 3)
    assert measures.win_rate_range(trades) == pytest.approx([lo * 100, hi * 100])
    assert measures.win_rate_range(trades.iloc[0:0]) is None


# ------------------------------------------------------------------ parameter stability


def grid_rows(scores, axes):
    import itertools
    return [{"values": list(v), "error": None, "score": s} for v, s in zip(itertools.product(*axes), scores)]


def test_neighbours_in_the_middle_of_a_level_grid_form_a_plateau():
    rows = grid_rows([9, 9, 9, 9, 10, 9, 9, 9, 9], [[1, 2, 3], [10, 20, 30]])
    st = analysis.neighbours(rows, rows[4])
    assert (st["neighbours"], st["scored"], st["below"]) == (8, 8, 8)
    assert st["median"] == 9 and st["share"] == pytest.approx(0.9) and st["shape"] == "plateau"


def test_a_corner_peak_and_option_settings_held_fixed():
    rows = grid_rows([10, 2, 1, 2, None, 3, 0, 0, 0, 0, 0, 0], [[1, 2, 3], ["a", "b"], [5, 6]])
    best = rows[0]  # (1, "a", 5)
    st = analysis.neighbours(rows, best)
    # neighbours keep "a": (1,a,6), (2,a,5), (2,a,6); (2,a,5) has no score
    assert st["neighbours"] == 3 and st["scored"] == 2 and st["settings"] == [0, 2]
    assert st["shape"] == "peak" and st["low"] == 2 and st["high"] == 3
    assert analysis.neighbours(rows, None) is None
    only_options = grid_rows([1, 2], [["a", "b"]])
    assert analysis.neighbours(only_options, only_options[1]) is None


def test_negative_chosen_score_gets_numbers_but_no_shape():
    rows = grid_rows([-1, -2, -3], [[1, 2, 3]])
    st = analysis.neighbours(rows, rows[0])
    assert st["neighbours"] == 1 and st["shape"] is None and st["share"] is None


# ------------------------------------------------------------------ optimiser output


def test_optimiser_reports_corrections_stability_and_selection(optimised):
    out = optimised
    mt = out["multiple_testing"]
    assert mt["tests"] == out["tested"] == 6 and mt["raw"] >= mt["bh"] >= mt["bonferroni"]
    assert mt["chosen"]["trades"] == out["deflated"]["trades"]
    assert out["stability"]["neighbours"] >= 1 and out["stability"]["score"] == next(r["score"] for r in out["rows"] if r["values"] == out["best"])
    assert out["selection_note"].startswith("Picked as the best of 6 settings tried;")
    wf = out["walk_forward"]
    assert wf["picked_from"] == 6 and "best of 6 combinations tried" in wf["selection_note"]
    for text in (out["selection_note"], wf["selection_note"]):
        assert not any(w in text.lower() for w in ("broken", "weak", "degraded", "bad", "good"))


def test_compare_settings_walk_forward_names_the_number_tried():
    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    curves = [(v, pd.Series(1000 + np.arange(200) * v, index=idx, dtype=float)) for v in (1, 2, 3)]
    wf = analysis.walk_forward(curves, folds=3)
    assert wf["picked_from"] == 3 and "best of 3 values tried" in wf["selection_note"]


# ------------------------------------------------------------------ paper trading note


def test_weekly_report_says_paper_trading_is_a_simulation(tmp_path):
    r = weekly.build(StateStore(tmp_path / "state.db"))
    assert weekly.PAPER_NOTE in weekly.as_text(r)
    assert "partial fills" in weekly.as_html(r)


def test_paper_note_shown_on_forward_test_pages():
    util = (STATIC / "util.js").read_text(encoding="utf-8")
    assert "export const PAPER_NOTE" in util and "partial fills, requotes" in util
    dash = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert dash.count("PAPER_NOTE)") == 3  # running tests, finished tests, one test's page
    assert "PAPER_NOTE)" in (STATIC / "activity.js").read_text(encoding="utf-8")


# ------------------------------------------------------------------ robustness summary (JS)

FAKE_DOM = r"""
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const [dir, stub, data] = process.argv.slice(1);
await import(pathToFileURL(stub).href);
const { robustnessSummary, renderBacktest } = await import(pathToFileURL(dir + "/testing.js").href);
const d = JSON.parse(readFileSync(data, "utf-8"));
renderBacktest(d.backtest);  // the full results page, on the stub's stand-in browser: must not throw
class FakeNode {
  constructor(tag, text) { this.tag = tag; this.kids = []; this.text = text || ""; this.style = {}; this.dataset = {}; this.classList = { toggle() {}, add() {}, remove() {} }; }
  appendChild(k) { this.kids.push(k); return k; }
  insertBefore(k) { this.kids.unshift(k); return k; }
  setAttribute() {} addEventListener() {} removeChild() {} replaceWith() {} querySelectorAll() { return []; }
  get firstChild() { return this.kids[0] || null; }
  get textContent() { return this.text + this.kids.map((k) => k.textContent).join(""); }
  set textContent(v) { this.text = v; this.kids = []; }
}
globalThis.Node = FakeNode;
globalThis.document = { createElement: (t) => new FakeNode(t), createElementNS: (_, t) => new FakeNode(t), createTextNode: (s) => new FakeNode("#text", s) };
const text = (n) => (n ? n.textContent : null);
console.log(JSON.stringify({
  none: robustnessSummary({ backtest: null, luck: null, optimise: null }),
  bt: text(robustnessSummary({ backtest: d.backtest, luck: null, optimise: null })),
  all: text(robustnessSummary(d)),
}));
process.exit(0);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_robustness_summary_shows_only_the_parts_with_data(bars, optimised, tmp_path):
    from tradealerts.web.common import clean

    bt = compute.backtest(CSV_STRATEGY, bars, None, None, [], 70)
    luck = {"currency": "£", "start": bt["metrics"]["start"], "end": bt["metrics"]["end"], "passed": 3, "total": 4,
            "checks": [{"title": "Enough trades", "passed": True}, {"title": "Random markets", "passed": None}],
            "deflated": {"probability": 0.4, "trials": 2}, "regimes": None, "order": None}
    axes = [{"label": "RSI level", "type": "number"}, {"label": "Stop ATR", "type": "number"}]
    data = tmp_path / "data.json"
    data.write_text(json.dumps(clean({"backtest": bt, "luck": luck, "optimise": {"res": optimised, "axes": axes}})))
    out = subprocess.run(["node", "--input-type=module", "-e", FAKE_DOM, "--", str(STATIC), str(STUB), str(data)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["none"] is None
    for part in ("Robustness summary", "By calendar year", "By market conditions", "Sample size", "allowing for linked returns",
                 "Worst drop, same trades in other orders", "Backtest train / test split"):
        assert part in got["bt"], part
    assert "Parameter sensitivity" not in got["bt"] and "Luck check" not in got["bt"]
    for part in ("Parameter sensitivity, last optimiser run", "Neighbouring settings", "Bonferroni", "Benjamini–Hochberg",
                 "Picked as the best of 6 settings tried", "Optimiser, walk-forward", "3 of 4 checks passed", "Deflated Sharpe (luck check)"):
        assert part in got["all"], part
