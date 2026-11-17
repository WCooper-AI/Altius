/* Trading Alerts web interface: Comparing backtests side by side.
   ES modules: each file imports what it uses (boot.js is the page's entry, see web/app.py). */

import { $main, append, clear, day, GET, h, money, num, POST, signed, svg, toast } from "./util.js";
import { gridlines, periodPicker, pref, savePref } from "./testing.js";
import { expandable } from "./widgets.js";
import { setNav } from "./boot.js";

// --------------------------------------------------------------------- compare backtests side by side

const SERIES_TONES = ["s1", "s2", "s3", "s4"];
function multiChart(series, { title, fmt }) {
  const W = 560, H = 230, P = 6;
  const wrap = h("div", { class: "chart" });
  const readout = h("span", { class: "readout" });
  append(wrap, [h("div", { class: "chart-title" }, h("h3", {}, title), h("span", { class: "chart-tools" }, readout, expandable(wrap)))]);
  const all = series.flatMap((x) => x.points);
  const ts = all.map((p) => new Date(p[0]).getTime());
  const t0 = Math.min(...ts), t1 = Math.max(...ts);
  let lo = Math.min(0, ...all.map((p) => p[1])), hi = Math.max(0, ...all.map((p) => p[1]));
  const pad = (hi - lo || 1) * 0.06; lo -= pad; hi += pad;
  const x = (t) => P + ((t - t0) * (W - 2 * P)) / (t1 - t0 || 1);
  const y = (v) => P + ((hi - v) * (H - 2 * P)) / (hi - lo);
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
  s.appendChild(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 14, class: "chart-bg" }));
  gridlines(s, lo, hi, y, fmt, W, P);
  s.appendChild(svg("line", { x1: P, x2: W - P, y1: y(0), y2: y(0), class: "chart-base", "stroke-dasharray": "4 4" }));
  series.forEach((ser, i) => {
    const d = ser.points.map((p, k) => `${k ? "L" : "M"}${x(new Date(p[0]).getTime()).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
    s.appendChild(svg("path", { d, class: `chart-line ${SERIES_TONES[i]}`, pathLength: 1, "stroke-width": 2, "stroke-linejoin": "round" }));
  });
  s.addEventListener("pointermove", (e) => {
    const r = s.getBoundingClientRect();
    const t = t0 + (((e.clientX - r.left) / r.width) * W - P) / (W - 2 * P) * (t1 - t0);
    readout.textContent = series.map((ser) => {
      let best = ser.points[0];
      for (const p of ser.points) if (new Date(p[0]).getTime() <= t) best = p;
      return `${ser.name.slice(0, 18)} ${fmt(best[1])}`;
    }).join(" · ");
  });
  s.addEventListener("pointerleave", () => { readout.textContent = ""; });
  wrap.appendChild(s);
  wrap.appendChild(h("div", { class: "legend small" }, series.map((ser, i) => h("span", {}, h("span", { class: `swatch line ${SERIES_TONES[i]}` }), ser.name))));
  return wrap;
}

const MATCH_OPTIONS = [
  ["market", "Same market", "Every strategy trades the chosen strategy's market, with its money per point, sizes and margin. Each keeps its own candle size, because its rules were written for it."],
  ["account", "Same starting balance and risk rules", "Starting balance, account currency, risk per trade, compounding and loss limits are copied from the chosen strategy."],
  ["costs", "Same costs", "Spread, slippage, commission, funding and borrow are copied from the chosen strategy. On a different market a spread or slippage in price points means a different amount, so those two stay as they were there."],
];

export async function viewCompare() {
  setNav("strategies");
  const list = await GET("/strategies");
  const ok = list.strategies.filter((x) => !x.error);
  const chosen = new Set(pref("ta-compare", "").split(",").filter((id) => ok.some((x) => x.id === id)));
  const match = new Set(pref("ta-compare-match", "").split(",").filter((k) => MATCH_OPTIONS.some(([key]) => key === k)));
  const nameOf = (id) => { const x = ok.find((s) => s.id === id); return (x && x.data.name) || id; };
  const out = h("div");
  const period = periodPicker({ custom: true });
  const run = h("button", { class: "primary", type: "button" }, "Compare");
  const reference = h("select", { "aria-label": "Take the shared settings from" });
  const refField = h("div", { class: "field" }, h("label", {}, "Copy them from"), reference);
  const syncReference = () => {
    const keep = reference.value;
    clear(reference);
    for (const id of chosen) reference.appendChild(h("option", { value: id }, nameOf(id)));
    if (chosen.has(keep)) reference.value = keep;
    refField.hidden = !match.size || chosen.size < 2;
  };
  const picks = h("div", { class: "pick-list" }, ok.map((x) => h("label", { class: "pick" },
    h("input", { type: "checkbox", checked: chosen.has(x.id), onchange: (e) => {
      if (e.target.checked) {
        if (chosen.size >= 4) { e.target.checked = false; toast("Compare up to 4 at a time", true); return; }
        chosen.add(x.id);
      } else chosen.delete(x.id);
      savePref("ta-compare", [...chosen].join(","));
      syncReference();
    } }), h("span", {}, h("strong", {}, x.data.name || x.id), h("span", { class: "small muted" }, ` ${(x.data.market || {}).label || ""} · ${(x.data.market || {}).timeframe || ""}`)))));
  const matchBoxes = MATCH_OPTIONS.map(([key, label, tip]) => h("label", { class: "pick" },
    h("input", { type: "checkbox", checked: match.has(key), onchange: (e) => {
      if (e.target.checked) match.add(key); else match.delete(key);
      savePref("ta-compare-match", [...match].join(","));
      syncReference();
    } }), h("span", {}, h("strong", {}, label), h("span", { class: "small muted" }, " " + tip))));
  syncReference();
  run.addEventListener("click", async () => {
    if (chosen.size < 2) { toast("Tick 2 to 4 strategies", true); return; }
    run.disabled = true;
    const ids = [...chosen];
    const results = [];
    try {
      clear(out).appendChild(h("p", { class: "loading" }, "Lining up the settings…"));
      const setup = await POST("/compare/setup", { ids, reference: reference.value || ids[0],
        match: Object.fromEntries([...match].map((k) => [k, true])), ...period.value() });
      for (const [i, x] of setup.strategies.entries()) {
        clear(out).appendChild(h("p", { class: "loading" }, `Backtesting ${i + 1} of ${setup.strategies.length}…`));
        results.push({ id: x.id, name: x.name, res: await POST("/backtest", { data: x.data, ...setup.period }) });
      }
      savePref("ta-did-backtest", "1");
      clear(out).appendChild(renderCompare(results, { setup }));
    } catch (e) { clear(out).appendChild(h("p", { class: "error-box" }, e.message)); }
    finally { run.disabled = false; }
  });
  clear($main).appendChild(h("div", { class: "stack" },
    h("div", {}, h("a", { class: "back", href: "#/strategies" }, "All strategies")),
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Compare backtests"),
      h("p", { class: "muted dash-lede" }, "Backtest two to four strategies over the same dates and see them side by side. Tick the settings to make the same for all; anything changed to match is listed with the results."))),
    h("div", { class: "editor portfolio-layout" },
      h("div", { class: "panel" }, h("section", { class: "section" }, h("h2", {}, "Strategies"), picks),
        h("section", { class: "section" }, h("h2", {}, "Make the same for all"), h("div", { class: "pick-list" }, matchBoxes), refField),
        h("section", { class: "section" }, h("div", { class: "bt-controls" }, period, run))),
      h("div", { class: "panel" }, h("section", { class: "section" }, out)))));
}

/** Plain notes under a side-by-side comparison: what is the same for all, what was changed in
 * each strategy to make it so, and whether the tested periods came out different. */
function setupNotes(setup, results) {
  const items = [...setup.same];
  for (const x of setup.strategies) {
    if (x.changes.length) items.push(`Changed in ${x.name} to match: ${x.changes.join("; ")}.`);
    items.push(...x.notes);
  }
  const spans = new Set(results.map((r) => `${day(r.res.metrics.start)} to ${day(r.res.metrics.end)}`));
  if (spans.size > 1) {
    items.push("The tested periods came out different, because the price data starts or ends on different dates: " +
      results.map((r) => `${r.name} ${day(r.res.metrics.start)} to ${day(r.res.metrics.end)}`).join("; ") + ".");
  }
  return h("ul", { class: "small muted compare-notes" }, items.map((x) => h("li", {}, x)));
}

/** Side by side results. Nothing is marked best or worst, and two results get a Change column
 * (second minus first). With { setup } (strategies compared on the same footing) the table starts
 * with what each was tested on and the notes list what was made to match.
 * Rows: [label, format, value, format a change]. */
export function renderCompare(results, { versions = false, setup = null } = {}) {
  const series = results.map((r) => {
    const start = r.res.metrics.start_equity;
    return { name: r.name, points: r.res.equity.map((p) => [p[0], (p[1] / start - 1) * 100]) };
  });
  const pts = (d) => signed(d, " pts", 1);
  const worstDay = (m) => (m.periods && m.periods.day ? m.periods.day.worst : null);
  const rows = [
    ["Return", (m) => signed(m.total_return_pct, "%"), (m) => m.total_return_pct, pts],
    ["Yearly growth (CAGR)", (m) => (m.cagr_pct === null || m.cagr_pct === undefined ? "–" : signed(m.cagr_pct, "%")), (m) => m.cagr_pct, pts],
    ["Worst drawdown", (m) => num(m.max_drawdown_pct, 1) + "%", (m) => m.max_drawdown_pct, pts],
    ["Calmar", (m) => num(m.calmar), (m) => m.calmar, (d) => signed(d)],
    ["Worst day", (m) => signed(worstDay(m), "%"), worstDay, pts],
    ["Average of the worst 5% of days", (m) => signed(m.expected_shortfall_pct, "%"), (m) => m.expected_shortfall_pct, pts],
    ["Trades", (m) => String(m.trades), (m) => m.trades, (d) => signed(d, "", 0)],
    ["Won", (m) => (m.trades ? num(m.win_rate_pct, 0) + "%" : "–"), (m) => (m.trades ? m.win_rate_pct : null), pts],
    ["Profit factor", (m) => (!m.trades ? "–" : m.profit_factor === null || m.profit_factor === undefined ? "No losses" : num(m.profit_factor, 2)),
      (m) => (m.trades && (m.profit_factor === null || m.profit_factor === undefined) ? Infinity : m.profit_factor), (d) => signed(d)],
    ["Average trade", (m) => (m.avg_r === null || m.avg_r === undefined ? "–" : signed(m.avg_r, "R")), (m) => m.avg_r, (d) => signed(d, "R")],
  ];
  const info = setup ? [
    ["Market", (r) => (r.res.meta ? `${r.res.meta.market} · ${r.res.meta.timeframe}` : "–")],
    ["Tested", (r) => `${day(r.res.metrics.start)} to ${day(r.res.metrics.end)}`],
    ["Starting balance", (r) => money(r.res.metrics.start_equity, r.res.currency)],
  ] : [];
  const change = results.length === 2;
  const has = (v) => v !== null && v !== undefined && Number.isFinite(v);
  const diff = (val, chg) => {
    if (!val) return "–";
    const [a, b] = results.map((r) => val(r.res.metrics));
    return has(a) && has(b) ? chg(b - a) : "–";
  };
  const changeNote = change ? ` Change is ${results[1].name} minus ${results[0].name}; for rows in %, it is in percentage points (pts).` : "";
  const note = versions
    ? "Each version is backtested over the same period with its own settings." + changeNote
    : "Returns are shown in % of each one's starting balance. A number on its own doesn't say which is preferable: return, drawdown and trade count describe different things." + changeNote;
  return h("div", { class: "compare-out" },
    multiChart(series, { title: "Return over time (%)", fmt: (v) => signed(v, "%") }),
    h("div", { class: "table-wrap" }, h("table", { class: "trades compare-table" },
      h("thead", {}, h("tr", {}, h("th", {}), results.map((r, i) => h("th", { class: "num" }, h("span", { class: `swatch line ${SERIES_TONES[i]}` }), " ", r.name)),
        change ? h("th", { class: "num" }, "Change") : null)),
      h("tbody", {},
        info.map(([label, fmt]) => h("tr", {}, h("th", {}, label), results.map((r) => h("td", { class: "num" }, fmt(r))), change ? h("td", {}) : null)),
        rows.map(([label, fmt, val, chg]) => h("tr", {}, h("th", {}, label),
          results.map((r) => h("td", { class: "num" }, fmt(r.res.metrics))),
          change ? h("td", { class: "num" }, diff(val, chg)) : null))))),
    h("p", { class: "small muted" }, note),
    setup ? setupNotes(setup, results) : null);
}
