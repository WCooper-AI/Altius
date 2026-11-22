/* Trading Alerts web interface: Testing tools beside the editor: backtest, compare settings, luck check.
   ES modules: each file imports what it uses (boot.js is the page's entry, see web/app.py). */

import { append, clear, day, GET, h, isObj, money, num, POST, short, signed, stageTag, svg, toast, when } from "./util.js";
import { describeCondition, indicatorMeta, isBlank, renderParts } from "./describe.js";
import { numberInput, PARAM_LABELS } from "./editor.js";
import { groupItems, groupKind } from "./nodegraph.js";
import { infoTip, STOP_KINDS, TARGET_KINDS } from "./strategies.js";
import { isHosted } from "./settings.js";
import { candleChart, expandable } from "./widgets.js";

// --------------------------------------------------------------------- backtest panel

// ----- test period picker, shared by both backtest panels

const PERIODS = [1, 2, 3, 5, 10];
export function pref(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; } }
export function savePref(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* not saved */ } }

/** Select for "Last N years" (and optionally custom dates). value() gives the request fields. */
export function periodPicker({ custom = false } = {}) {
  const sel = h("select", { "aria-label": "Test period" },
    PERIODS.map((y) => h("option", { value: String(y) }, y === 1 ? "Last year" : `Last ${y} years`)),
    custom ? h("option", { value: "custom" }, "Custom dates") : null);
  const saved = pref("ta-bt-years", "2");
  sel.value = PERIODS.map(String).includes(saved) || (custom && saved === "custom") ? saved : "2";
  const from = h("input", { type: "date", "aria-label": "From" });
  const to = h("input", { type: "date", "aria-label": "To" });
  const dates = h("span", { class: "inline period-dates" },
    h("span", { class: "field" }, h("label", {}, "From"), from), h("span", { class: "field" }, h("label", {}, "To"), to));
  const sync = () => { dates.hidden = sel.value !== "custom"; };
  sel.addEventListener("change", () => { savePref("ta-bt-years", sel.value); sync(); });
  sync();
  const el = h("span", { class: "inline period" }, h("span", { class: "field" }, h("label", {}, "Test over"), sel), custom ? dates : null);
  el.value = () => (sel.value === "custom"
    ? { start: from.value || null, end: to.value || null }
    : { years: Number(sel.value) });
  return el;
}

// ----- testing tools: backtest, compare settings, luck check

export function setPath(obj, path, value) {
  let node = obj;
  for (const k of path.slice(0, -1)) node = node[k];
  node[path[path.length - 1]] = value;
}

/** Numbers in a custom strategy's rules that "Compare settings" can vary. */
export function pathSettings(d) {
  const out = [];
  const add = (path, label, v, integer = false) => {
    if (typeof v === "number") out.push({ kind: "path", path, label, type: "number", integer, current: v });
  };
  const walkOperand = (op, path, where) => {
    if (typeof op === "number") add(path, `${where}: the number ${short(op)}`, op);
    else if (isObj(op) && op.indicator) {
      const meta = indicatorMeta(op.indicator);
      const name = meta ? meta.short : op.indicator;
      for (const [k, v] of Object.entries(op)) {
        if (typeof v === "number" && !["shift", "scale", "add"].includes(k)) {
          add([...path, k], `${where}: ${name} ${PARAM_LABELS[k] || k}`, v, !!meta && meta.types[k] === "int");
        }
      }
    }
  };
  const walkCond = (c, path, where) => {
    if (!isObj(c)) return;
    const kind = groupKind(c);
    if (kind) {
      const items = groupItems(c);
      const base = { all: ["all"], any: ["any"], none: ["not", "any"], notall: ["not", "all"] }[kind];
      items.forEach((it, i) => walkCond(it, [...path, ...base, i], items.length > 1 ? `${where}, rule ${i + 1}` : where));
      return;
    }
    if ("left" in c) { walkOperand(c.left, [...path, "left"], where); walkOperand(c.right, [...path, "right"], where); }
  };
  walkCond(d.entry, ["entry"], "Entry");
  const ex = d.exit || {};
  for (const [which, label, kinds] of [["stop", "Stop loss", STOP_KINDS], ["target", "Take profit", TARGET_KINDS]]) {
    const spec = ex[which];
    if (!isObj(spec)) continue;
    for (const k of ["atr", "percent", "points", "r_multiple"]) if (typeof spec[k] === "number") add(["exit", which, k], `${label}: ${kinds[k]}`, spec[k]);
    if (typeof spec.atr_period === "number") add(["exit", which, "atr_period"], `${label}: ATR bars`, spec.atr_period, true);
    if (spec.level) walkOperand(spec.level, ["exit", which, "level"], label);
  }
  if (typeof ex.max_bars === "number") add(["exit", "max_bars"], "Close after this many candles", ex.max_bars, true);
  if (ex.when) walkCond(ex.when, ["exit", "when"], "Exit rule");
  return out;
}

/** A guided strategy's settings that "Compare settings" can vary. */
export function knobSettings(recipe, st) {
  const out = [];
  for (const k of recipe.knobs) {
    const base = { kind: "knob", key: k.key, label: k.label + (k.unit ? ` (${k.unit})` : ""), current: st[k.key] };
    if (k.kind === "number") out.push({ ...base, type: "number", integer: !!k.integer || k.key === "max_hold", min: k.min, max: k.max });
    else if (k.kind === "select") out.push({ ...base, type: "select", options: k.options });
    else if (k.kind === "toggle") out.push({ ...base, type: "select", options: [{ value: false, label: "Off" }, { value: true, label: "On" }] });
  }
  return out;
}

const MAX_SWEEP = 25;
function decimals(x) { const t = String(x); return t.includes(".") ? t.split(".")[1].length : 0; }
function rangeValues(from, to, step, integer) {
  if ([from, to, step].some(isBlank)) return { values: [], note: "Fill in from, to and step." };
  if (step <= 0) return { values: [], note: "The step must be more than 0." };
  if (to <= from) return { values: [], note: "'To' must be bigger than 'from'." };
  const n = Math.floor((to - from) / step + 1e-9) + 1;
  if (n > MAX_SWEEP) return { values: [], note: `That's ${n} values. Use a bigger step: ${MAX_SWEEP} at most.` };
  const d = Math.max(decimals(from), decimals(step));
  let values = Array.from({ length: n }, (_, i) => +(from + i * step).toFixed(d));
  if (integer) values = [...new Set(values.map(Math.round))];
  if (values.length < 2) return { values: [], note: "That gives fewer than two values." };
  return { values, note: `Tests ${values.length} values: ${values.slice(0, 6).map(short).join(", ")}${values.length > 6 ? ", …" : ""}` };
}

/** Backtest, Compare settings and Luck check, sharing one test period. */
export function analysisPanel({ getData, render, intro, settings, apply, runLabel = "Run backtest", strategyId = null, onRan = null }) {
  const period = periodPicker({ custom: true });
  const TABS = [["backtest", "Backtest"], ["sweep", "Compare settings"], ["optimise", "Optimiser"], ["luck", "Luck check"]];
  let current = pref("ta-tool", "backtest");
  if (!TABS.some(([k]) => k === current)) current = "backtest";
  const seg = h("div", { class: "segmented tabs", role: "tablist", "aria-label": "Testing tools" });
  const holder = h("div", { class: "tool-body" });

  async function fetchData() {
    const d = await getData();
    return d || null;
  }

  // --- robustness summary: the latest result of each tool, gathered in one place
  const last = { backtest: null, luck: null, optimise: null };
  const summary = h("section", { class: "section robust-summary", hidden: true });
  function remember(kind, value) {
    last[kind] = value;
    const node = robustnessSummary(last);
    clear(summary);
    summary.hidden = !node;
    if (node) summary.appendChild(node);
  }

  // --- backtest
  const btOut = h("div", {}, h("p", { class: "muted small" }, intro));
  const btRun = h("button", { class: "primary", type: "button" }, runLabel);
  btRun.addEventListener("click", async () => {
    const data = await fetchData();
    if (!data) return;
    btRun.disabled = true;
    clear(btOut).appendChild(h("p", { class: "loading" }, "Loading prices and testing your rules. Longer periods take longer the first time…"));
    try {
      const body = { data, strategy_id: strategyId, ...period.value() };
      if (split) body.split_pct = split;
      const res = await POST("/backtest", body);
      savePref("ta-did-backtest", "1");
      clear(btOut).appendChild(render(res));
      remember("backtest", res);
      btRun.textContent = "Test again";
      if (onRan) onRan("backtest");
    } catch (e) { clear(btOut).appendChild(h("p", { class: "error-box" }, e.message)); }
    finally { btRun.disabled = false; }
  });
  let split = Number(pref("ta-bt-split", "0")) || 0;
  const splitPick = h("select", { "aria-label": "Train / test split" }, SPLIT_OPTIONS.map(([v, label]) => h("option", { value: String(v), selected: v === split ? "" : null }, label)));
  splitPick.addEventListener("change", () => { split = Number(splitPick.value); savePref("ta-bt-split", String(split)); });
  const backtestPane = h("section", { class: "section" }, h("div", { class: "tool-actions with-field" }, btRun,
    h("div", { class: "field" }, h("label", {}, "Train / test split", infoTip("Tests the first part of the period as usual, then trades the last part again from a fresh balance. Rules tuned on the first part that do much worse on the last part may be fitted to the past.")), splitPick)), btOut);

  // --- compare settings
  let list = [];
  let chosen = null;
  const pick = h("select", { class: "wide", "aria-label": "Setting to compare" });
  const rangeBox = h("div", { class: "range-box" });
  const swOut = h("div");
  const swRun = h("button", { class: "primary", type: "button" }, "Compare");
  let range = { from: null, to: null, step: null };
  let values = [];
  const rangeNote = h("p", { class: "small muted", "aria-live": "polite" });
  function refreshSettings() {
    list = settings();
    const keep = chosen && list.findIndex((x) => x.label === chosen.label);
    clear(pick);
    pick.appendChild(h("option", { value: "", disabled: true }, list.length ? "Choose a setting…" : "Fill in some settings first"));
    list.forEach((x, i) => pick.appendChild(h("option", { value: String(i) }, x.label)));
    pick.value = keep !== null && keep >= 0 ? String(keep) : "";
    chosen = pick.value === "" ? null : list[Number(pick.value)];
    pick.classList.toggle("to-fill", !chosen);
    renderRange();
  }
  pick.addEventListener("change", () => { chosen = list[Number(pick.value)]; pick.classList.remove("to-fill"); range = { from: null, to: null, step: null }; renderRange(); });
  pick.addEventListener("focus", () => { const before = pick.value; refreshSettings(); if (pick.value !== before && before) pick.value = before; });
  function renderRange() {
    clear(rangeBox);
    values = [];
    if (!chosen) { rangeNote.textContent = ""; return; }
    const now = chosen.current;
    if (chosen.type === "select") {
      values = chosen.options.map((o) => o.value);
      rangeNote.textContent = `Tests every option: ${chosen.options.map((o) => o.label).join(", ")}.`;
      return;
    }
    const upd = () => { const r = rangeValues(range.from, range.to, range.step, chosen.integer); values = r.values; rangeNote.textContent = r.note; };
    const field = (key, label) => h("div", { class: "field" }, h("label", {}, label),
      numberInput(range[key], (n) => { range[key] = n; upd(); }, { label, step: "any", cls: "narrow", allowEmpty: false }));
    append(rangeBox, [h("div", { class: "inline range-fields" }, field("from", "From"), field("to", "To"), field("step", "Step")),
      isBlank(now) ? null : h("p", { class: "small muted" }, `Currently ${short(now)}.`)]);
    upd();
  }
  swRun.addEventListener("click", async () => {
    if (!chosen) { toast("Choose a setting to compare", true); return; }
    if (values.length < 2) { toast(rangeNote.textContent || "Choose the values to compare", true); return; }
    const data = await fetchData();
    if (!data) return;
    swRun.disabled = true;
    const setting = chosen.kind === "knob" ? { kind: "knob", key: chosen.key } : { kind: "path", path: chosen.path };
    clear(swOut).appendChild(h("p", { class: "loading" }, `Running ${values.length} backtests. This can take a minute…`));
    try {
      const res = await POST("/sweep", { data, setting, values, strategy_id: strategyId, ...period.value() });
      clear(swOut).appendChild(renderSweep(res, chosen, apply ? (v) => { apply(chosen, v); chosen = { ...chosen, current: v }; toast(`${chosen.label} set to ${optionLabel(chosen, v)}`); } : null));
      if (onRan) onRan("sweep");
    } catch (e) { clear(swOut).appendChild(h("p", { class: "error-box" }, e.message)); }
    finally { swRun.disabled = false; }
  });
  const sweepPane = h("section", { class: "section" },
    h("p", { class: "muted small tool-intro" }, "Runs the same backtest with one setting changed, to see how sensitive the result is to it."),
    h("div", { class: "field" }, h("label", {}, "Setting"), pick), rangeBox, rangeNote,
    h("div", { class: "tool-actions" }, swRun), swOut);

  // --- optimiser
  const opt = optimiserPane({ fetchData, settings, apply, period, strategyId, onRan, onResult: (res, axes) => remember("optimise", { res, axes }) });

  // --- luck check
  const lkOut = h("div");
  let trials = null;
  const lkRun = h("button", { class: "primary", type: "button" }, "Run luck check");
  lkRun.addEventListener("click", async () => {
    const data = await fetchData();
    if (!data) return;
    lkRun.disabled = true;
    clear(lkOut).appendChild(h("p", { class: "loading" }, "Backtesting on up to 200 random price paths and reshuffling the trades. This takes up to a minute…"));
    try {
      const res = await POST("/luck", { data, trials, strategy_id: strategyId, ...period.value() });
      clear(lkOut).appendChild(renderLuck(res));
      remember("luck", res);
      lkRun.textContent = "Check again";
      if (onRan) onRan("luck");
    } catch (e) { clear(lkOut).appendChild(h("p", { class: "error-box" }, e.message)); }
    finally { lkRun.disabled = false; }
  });
  const luckPane = h("section", { class: "section" },
    h("p", { class: "muted small tool-intro" }, "Asks whether the backtest's result could just be luck: too few trades, all the profit from one stretch, " +
      "a handful of big winners, or rules that do just as well on made-up prices."),
    h("div", { class: "field" }, h("label", {}, "Settings tried so far", infoTip("How many versions or settings of these rules you have backtested, counting this one. " +
      "The more you try, the more likely the best of them looks good by chance, so the deflated Sharpe ratio raises its bar to match. Leave blank for 1.")),
      numberInput(null, (n) => { trials = n; }, { label: "Settings tried so far", min: 1, cls: "narrow", integer: true, allowEmpty: true, placeholder: "1" })),
    h("div", { class: "tool-actions" }, lkRun), lkOut);

  const panes = { backtest: backtestPane, sweep: sweepPane, optimise: opt.pane, luck: luckPane };
  function show(k) {
    current = k;
    savePref("ta-tool", k);
    clear(seg);
    for (const [key, label] of TABS) {
      seg.appendChild(h("button", { type: "button", role: "tab", "aria-selected": String(key === k), "aria-pressed": String(key === k),
        onclick: () => show(key) }, label));
    }
    clear(holder).appendChild(panes[k]);
    if (k === "sweep") refreshSettings();
    if (k === "optimise") opt.refresh();
  }
  show(current);
  const panel = h("div", { class: "panel tools" },
    h("section", { class: "section tool-top" }, seg, h("div", { class: "bt-controls" }, period)), holder, summary);
  panel.markStale = () => {
    for (const out of [btOut, swOut, opt.out, lkOut]) {
      if (out.querySelector(".plain-results, .headline, .sweep, .optimiser, .luck") && !out.querySelector(".stale")) {
        out.insertBefore(h("p", { class: "notice stale" }, "You've changed the settings. Run it again to update these results."), out.firstChild);
      }
    }
    if (!summary.hidden && !summary.querySelector(".stale"))
      summary.insertBefore(h("p", { class: "notice stale" }, "You've changed the settings since these results were made. Run the tools again to update them."), summary.firstChild);
  };
  panel.show = show; // open one of the tools from elsewhere on the page (the stage strip's "Go there")
  return panel;
}

/** Optimiser: every combination of up to three settings, run in the background with progress and a Stop button. */
const MAX_OPT_SETTINGS = 3;
const SCORE_OPTIONS = [["return", "Return"], ["return_dd", "Return ÷ worst drop"], ["profit_factor", "Profit factor"], ["sharpe", "Steadiness (Sharpe)"]];
const SPLIT_OPTIONS = [[0, "Off: test the whole period"], [60, "60% in-sample / 40% out-of-sample"], [70, "70% in-sample / 30% out-of-sample"], [80, "80% in-sample / 20% out-of-sample"]];
const HOLDOUT_OPTIONS = [[0, "Nothing: rank on the whole period"], [20, "The last 20% of the period"], [30, "The last 30% of the period"], [40, "The last 40% of the period"], [50, "The last half of the period"]];
const FOLD_OPTIONS = [[0, "Off"], [3, "3 stretches"], [4, "4 stretches"], [5, "5 stretches"], [6, "6 stretches"]];
function comboCap() { return typeof isHosted === "function" && isHosted() ? 200 : 2000; }

function optimiserPane({ fetchData, settings, apply, period, strategyId = null, onRan = null, onResult = null }) {
  let list = [];
  let axes = [newAxis()];
  let score = pref("ta-opt-score", "return");
  let minTrades = 30;
  let holdout = Number(pref("ta-opt-holdout", "0")) || 0;
  let folds = Number(pref("ta-opt-folds", "0")) || 0;
  let job = null;
  const axesBox = h("div", { class: "opt-axes" });
  const note = h("p", { class: "small muted", "aria-live": "polite" });
  const out = h("div");
  const progress = h("div", { class: "opt-progress", hidden: true });
  const run = h("button", { class: "primary", type: "button" }, "Find the best combination");
  const addBtn = h("button", { type: "button", class: "quiet", onclick: () => { if (axes.length < MAX_OPT_SETTINGS) { axes.push(newAxis()); renderAxes(); } } }, "+ Add a setting");

  function newAxis() { return { chosen: null, range: { from: null, to: null, step: null }, values: [] }; }
  function refresh() {
    list = settings();
    for (const a of axes) {
      if (!a.chosen) continue;
      const i = list.findIndex((x) => x.label === a.chosen.label);
      a.chosen = i >= 0 ? list[i] : null;
    }
    renderAxes();
  }
  function axisValues(a) {
    if (!a.chosen) return { values: [], note: "" };
    if (a.chosen.type === "select") return { values: a.chosen.options.map((o) => o.value), note: `Every option: ${a.chosen.options.map((o) => o.label).join(", ")}.` };
    return rangeValues(a.range.from, a.range.to, a.range.step, a.chosen.integer);
  }
  function combos() { return axes.reduce((n, a) => n * (a.values.length || 0), 1); }
  function updateNote() {
    const ready = axes.filter((a) => a.chosen);
    if (!ready.length || ready.some((a) => !a.values.length)) { note.textContent = ""; return; }
    const n = combos();
    const cap = comboCap();
    note.textContent = n > cap ? `That's ${n.toLocaleString()} combinations. This edition runs ${cap.toLocaleString()} at most in one go: use bigger steps or fewer values.`
      : `${n.toLocaleString()} combination${n === 1 ? "" : "s"} to test${n > 300 ? " (this can take several minutes)" : ""}.`;
    note.classList.toggle("loss", n > cap);
  }
  function renderAxes() {
    clear(axesBox);
    axes.forEach((a, idx) => {
      const pick = h("select", { class: "wide", "aria-label": `Setting ${idx + 1}` });
      pick.appendChild(h("option", { value: "", disabled: true }, list.length ? "Choose a setting…" : "Fill in some settings first"));
      list.forEach((x, i) => pick.appendChild(h("option", { value: String(i), disabled: axes.some((o) => o !== a && o.chosen && o.chosen.label === x.label) }, x.label)));
      pick.value = a.chosen ? String(list.findIndex((x) => x.label === a.chosen.label)) : "";
      pick.classList.toggle("to-fill", !a.chosen);
      const fields = h("div", { class: "range-box" });
      const axisNote = h("p", { class: "small muted" });
      const upd = () => { const r = axisValues(a); a.values = r.values; axisNote.textContent = r.note; updateNote(); };
      const drawFields = () => {
        clear(fields);
        if (a.chosen && a.chosen.type !== "select") {
          const field = (key, label) => h("div", { class: "field" }, h("label", {}, label),
            numberInput(a.range[key], (n) => { a.range[key] = n; upd(); }, { label, step: "any", cls: "narrow", allowEmpty: false }));
          append(fields, [h("div", { class: "inline range-fields" }, field("from", "From"), field("to", "To"), field("step", "Step")),
            isBlank(a.chosen.current) ? null : h("p", { class: "small muted" }, `Currently ${short(a.chosen.current)}.`)]);
        }
        upd();
      };
      pick.addEventListener("change", () => { a.chosen = list[Number(pick.value)]; a.range = { from: null, to: null, step: null }; pick.classList.remove("to-fill"); drawFields(); renderAxes(); });
      drawFields();
      axesBox.appendChild(h("div", { class: "opt-axis" },
        h("div", { class: "field" }, h("label", {}, `Setting ${idx + 1}`), h("div", { class: "inline" }, pick,
          axes.length > 1 ? h("button", { type: "button", class: "quiet", "aria-label": "Remove this setting", onclick: () => { axes.splice(idx, 1); renderAxes(); } }, "Remove") : null)),
        fields, axisNote));
    });
    addBtn.hidden = axes.length >= MAX_OPT_SETTINGS;
    updateNote();
  }

  function showProgress(st) {
    const total = st && st.total ? st.total : 0;
    const done = st ? st.done : 0;
    const pct = total ? Math.round(done / total * 100) : 0;
    progress.hidden = false;
    clear(progress);
    append(progress, [
      h("div", { class: "inline", style: "justify-content:space-between;align-items:center;gap:1rem" },
        h("p", { class: "small muted", style: "margin:0" }, st && st.status === "queued" ? "Waiting for a free worker…" : total ? `${done.toLocaleString()} of ${total.toLocaleString()} combinations tested (${pct}%)` : "Starting…"),
        h("button", { type: "button", class: "quiet", onclick: stop }, "Stop")),
      h("div", { class: "progress bt-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct) },
        h("span", { class: "progress-fill", style: `width:${pct}%` })),
    ]);
  }
  async function stop() {
    if (!job) return;
    try { await POST(`/jobs/${encodeURIComponent(job)}/cancel`, {}); } catch (e) { toast(e.message, true); }
  }
  async function poll(id, chosenAxes) {
    while (job === id) {
      let st;
      try { st = await GET(`/jobs/${encodeURIComponent(id)}`); } catch (e) { finish(); clear(out).appendChild(h("p", { class: "error-box" }, e.message)); return; }
      if (st.status === "queued" || st.status === "running") { showProgress(st); await new Promise((r) => setTimeout(r, 700)); continue; }
      finish();
      if (st.status === "completed") {
        clear(out).appendChild(renderOptimiser(st.result, chosenAxes, apply ? applyRow : null));
        if (onResult) onResult(st.result, chosenAxes);
      }
      else if (st.status === "cancelled") clear(out).appendChild(h("p", { class: "notice" }, `Stopped after ${st.done.toLocaleString()} of ${st.total.toLocaleString()} combinations.`));
      else clear(out).appendChild(h("p", { class: "error-box" }, st.error || "The optimiser run failed."));
      return;
    }
  }
  function finish() { job = null; progress.hidden = true; run.disabled = false; }
  function applyRow(chosenAxes, values) {
    chosenAxes.forEach((a, i) => { apply(a, values[i]); a.current = values[i]; });
    toast("Settings applied. Save the strategy to keep them.");
  }
  run.addEventListener("click", async () => {
    const ready = axes.filter((a) => a.chosen);
    if (!ready.length) { toast("Choose a setting to optimise", true); return; }
    if (ready.some((a) => !a.values.length)) { toast("Fill in the values for each setting", true); return; }
    if (combos() > comboCap()) { toast(note.textContent, true); return; }
    const data = await fetchData();
    if (!data) return;
    const chosenAxes = ready.map((a) => ({ ...a.chosen }));
    const body = {
      data, score, min_trades: minTrades, holdout_pct: holdout, walk_forward: folds, strategy_id: strategyId, ...period.value(),
      settings: ready.map((a) => ({ setting: a.chosen.kind === "knob" ? { kind: "knob", key: a.chosen.key } : { kind: "path", path: a.chosen.path }, values: a.values })),
    };
    run.disabled = true;
    clear(out);
    showProgress(null);
    try {
      const r = await POST("/optimise", body);
      job = r.job;
      if (onRan) onRan("optimise");
      poll(job, chosenAxes);
    } catch (e) { finish(); clear(out).appendChild(h("p", { class: "error-box" }, e.message)); }
  });

  const scorePick = h("select", { "aria-label": "Rank by" }, SCORE_OPTIONS.map(([k, label]) => h("option", { value: k, selected: k === score ? "" : null }, label)));
  scorePick.addEventListener("change", () => { score = scorePick.value; savePref("ta-opt-score", score); });
  const holdoutPick = h("select", { "aria-label": "Keep back" }, HOLDOUT_OPTIONS.map(([v, label]) => h("option", { value: String(v), selected: v === holdout ? "" : null }, label)));
  holdoutPick.addEventListener("change", () => { holdout = Number(holdoutPick.value); savePref("ta-opt-holdout", String(holdout)); });
  const foldsPick = h("select", { "aria-label": "Walk-forward check" }, FOLD_OPTIONS.map(([v, label]) => h("option", { value: String(v), selected: v === folds ? "" : null }, label)));
  foldsPick.addEventListener("change", () => { folds = Number(foldsPick.value); savePref("ta-opt-folds", String(folds)); });
  const pane = h("section", { class: "section" },
    h("p", { class: "muted small tool-intro" }, "Tests every combination of up to three settings and ranks them. Runs in the background: you can stop it at any time."),
    axesBox, addBtn,
    h("div", { class: "inline" },
      h("div", { class: "field" }, h("label", {}, "Rank by"), scorePick),
      h("div", { class: "field" }, h("label", {}, "Needs at least"), h("div", { class: "inline" },
        numberInput(minTrades, (n) => { minTrades = n ?? 0; }, { label: "Minimum trades", min: 0, cls: "narrow", integer: true, allowEmpty: false, emptyValue: 0 }), h("span", { class: "small muted" }, "trades")))),
    h("div", { class: "inline" },
      h("div", { class: "field" }, h("label", {}, "Keep back", infoTip("Ranks the combinations on the first part of the period only, then shows how the winner did on the last part, which it never saw. A winner that holds up there is more likely to hold up live.")), holdoutPick),
      h("div", { class: "field" }, h("label", {}, "Walk-forward check", infoTip("Splits the period into stretches. Before each stretch it picks the combination that scored best on all the data so far, then records how that combination did on the stretch. Every choice only uses the past, like tuning for real would.")), foldsPick)),
    note, h("div", { class: "tool-actions" }, run), progress, out);
  return { pane, out, refresh };
}

const has = (v) => v !== null && v !== undefined;

/** Robustness summary: results by calendar year and market state, sample-size ranges, the spread of the worst drop,
    earlier vs kept-back results and parameter sensitivity, from the latest backtest, luck check and optimiser run.
    Only the parts whose tool has run are shown. Descriptive only. */
export function robustnessSummary({ backtest: bt, luck, optimise: op }) {
  const m = bt && bt.metrics ? bt.metrics : null;
  const sym = (bt || luck || {}).currency || "";
  const tone = (v) => (v > 0 ? "gain" : v < 0 ? "loss" : "");
  const parts = [];
  const part = (title, tip, ...kids) => parts.push(h("div", { class: "walk" }, h("h3", {}, title, tip ? infoTip(tip) : null), ...kids));

  if (m && m.by_year && m.by_year.length) part("By calendar year",
    "Change in the balance over each calendar year (UTC) of the backtest, the deepest drop within that year, and the trades that closed in it. The first and last years can be part-years.",
    h("div", { class: "table-wrap" }, h("table", { class: "trades sweep-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Year"), ["Balance change", "Worst drop", "Trades closed", "Won", "Profit / loss", "Average trade"].map((x) => h("th", { class: "num" }, x)))),
      h("tbody", {}, m.by_year.map((y) => h("tr", {}, h("th", { scope: "row" }, String(y.year)),
        h("td", { class: "num " + tone(y.change_pct) }, signed(y.change_pct, "%", 1)),
        h("td", { class: "num" }, has(y.max_drawdown_pct) ? num(y.max_drawdown_pct, 1) + "%" : "–"),
        h("td", { class: "num" }, String(y.trades)),
        h("td", { class: "num" }, has(y.won_pct) ? num(y.won_pct, 0) + "%" : "–"),
        h("td", { class: "num " + tone(y.pnl) }, y.trades ? money(y.pnl, sym) : "–"),
        h("td", { class: "num" }, has(y.avg_r) ? signed(y.avg_r, "R") : "–")))))));

  const reg = (bt && bt.regimes) || (luck && luck.regimes);
  if (reg) parts.push(renderRegimes(reg, sym));

  if (m && m.trades) {
    const rr = m.ratio_ranges || {};
    const band = (b) => `${num(b[0])} to ${num(b[1])}`;
    const lines = [["Trades", String(m.trades)]];
    if (m.win_rate_range) lines.push(["Won", `${num(m.win_rate_pct, 0)}%; 95% range ${num(m.win_rate_range[0], 0)}% to ${num(m.win_rate_range[1], 0)}% (Wilson)`]);
    if (has(m.sharpe)) lines.push(["Sharpe (yearly)", num(m.sharpe) + (m.sharpe_ci ? `; 95% range ${band(m.sharpe_ci)} taking trades as independent` : "") +
      (rr.sharpe ? `; ${band(rr.sharpe)} allowing for linked returns` : "")]);
    if (has(m.sortino)) lines.push(["Sortino (yearly)", num(m.sortino) + (rr.sortino ? `; 95% range ${band(rr.sortino)} allowing for linked returns` : "")]);
    if (luck && luck.deflated) lines.push(["Deflated Sharpe (luck check)", `${pctOf(luck.deflated.probability)} chance the true per-trade Sharpe ratio is above the luck bar, counting ${luck.deflated.trials} ${luck.deflated.trials === 1 ? "try" : "tries"}`]);
    part("Sample size", "Each range shows how far the figure could move with a different run of trades like these. Fewer trades give wider ranges. " +
      "The linked-returns ranges resample runs of candles in a row, so a trade's candles and overlapping trades stay together.",
      m.sample_warning ? h("p", { class: "sample-warning", role: "note" }, m.sample_warning) : null,
      h("dl", { class: "metrics" }, lines.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      m.overlapping_trades ? h("p", { class: "small muted" }, `${m.overlapping_trades} trade${m.overlapping_trades === 1 ? "" : "s"} opened while another was still open, so their results are linked.`) : null);
  }

  const mc = bt && bt.monte_carlo;
  const lo = luck && luck.order;
  const spread = mc ? { runs: mc.runs, actual: mc.actual_dd, mid: mc.p50, bad: mc.p5, good: mc.p95 }
    : lo ? { runs: lo.runs, actual: lo.actual_dd, mid: lo.median_dd, bad: lo.bad_dd, good: lo.good_dd } : null;
  if (spread) part("Worst drop, same trades in other orders", "The trades shuffled into random orders. How deep the worst drop gets depends on which trades happen to come together.",
    h("p", {}, `In this backtest's order the worst drop was ${num(Math.abs(spread.actual), 1)}%. Across ${spread.runs.toLocaleString("en-GB")} reshuffled orders it was typically `,
      h("strong", {}, `${num(Math.abs(spread.mid), 1)}%`), `; 1 order in 20 reached ${num(Math.abs(spread.bad), 1)}% or deeper, and 1 in 20 stayed under ${num(Math.abs(spread.good), 1)}%.`));

  const cmp = [];
  if (m && bt.oos_metrics) {
    const o = bt.oos_metrics;
    cmp.push(["Backtest train / test split", `${signed(m.total_return_pct, "%")} over ${m.trades} trades`, `${signed(o.total_return_pct, "%")} over ${o.trades} trades (fresh balance)`]);
  }
  const ho = op && op.res.holdout;
  if (ho && ho.best) cmp.push(["Optimiser winner, kept-back part", `${signed(ho.best.train_return_pct, "%")} on the ranked part`, `${signed(ho.best.holdout_return_pct, "%")} over ${ho.best.holdout_trades} trades`]);
  const wf = op && op.res.walk_forward;
  if (wf && !wf.skipped) cmp.push(["Optimiser, walk-forward", `${signed(wf.hindsight_pct, "%")} for the best combination picked with hindsight`, `${signed(wf.walk_forward_pct, "%")} re-picking from earlier data before each stretch`]);
  if (cmp.length) part("Earlier results vs kept-back results", "The kept-back part was not used to choose or tune anything, so it is the closest a backtest gets to prices the rules haven't seen.",
    h("div", { class: "table-wrap" }, h("table", { class: "trades sweep-table" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Part looked at"), h("th", {}, "Part kept back"))),
      h("tbody", {}, cmp.map(([k, a, b]) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, a), h("td", {}, b)))))));

  if (op && op.res.best) {
    const r = op.res;
    const label = (vals) => op.axes.map((a, i) => `${a.label} ${optionLabel(a, vals[i])}`).join(", ");
    const fmtScore = (v) => (r.score === "return" ? signed(v, "%") : num(v, 2));
    part("Parameter sensitivity, last optimiser run", null,
      h("p", {}, "Chosen combination: ", h("strong", {}, label(r.best)), `, from ${day(r.start)} to ${day(r.end)}.`),
      r.selection_note ? h("p", { class: "small muted" }, r.selection_note) : null,
      r.overfitting ? h("p", { class: "small" }, `Probability of backtest overfitting: ${pctOf(r.overfitting.probability)}.`) : null);
    if (r.stability) parts.push(renderStability(r.stability, fmtScore));
    if (r.multiple_testing) parts.push(renderMultipleTesting(r.multiple_testing));
  }

  if (luck && luck.total) part("Luck check", null, h("p", {}, `${luck.passed} of ${luck.total} checks passed: ` +
    luck.checks.map((c) => `${c.title} (${c.passed === null ? "not run" : c.passed ? "passed" : "not passed"})`).join("; ") + "."));

  if (!parts.length) return null;
  const from = [m ? `backtest ${day(m.start)} to ${day(m.end)}` : null, luck ? `luck check ${day(luck.start)} to ${day(luck.end)}` : null,
    op ? `optimiser ${day(op.res.start)} to ${day(op.res.end)}` : null].filter(Boolean);
  return h("div", {}, h("h2", {}, "Robustness summary"), stageTag("robust"),
    h("p", { class: "small muted" }, `Gathered from the latest results on this page (${from.join("; ")}). A part appears once the tool it comes from has run. ` +
      "All of it describes past prices; none of it predicts future results."),
    ...parts);
}

function renderOptimiser(res, axes, onApply) {
  const box = h("div", { class: "optimiser" });
  const rows = res.rows;
  const scored = rows.filter((r) => r.score !== null && r.score !== undefined);
  const label = (vals) => axes.map((a, i) => `${a.label} ${optionLabel(a, vals[i])}`).join(", ");
  const scoreName = (SCORE_OPTIONS.find(([k]) => k === res.score) || [])[1] || "Score";
  const fmtScore = (v) => res.score === "return" ? signed(v, "%") : num(v, 2);
  if (!scored.length) {
    box.appendChild(h("p", { class: "error-box" }, rows.length && rows.every((r) => r.error) ? "None of the combinations could be tested: " + rows[0].error
      : "No combination made any trades in this period."));
    return box;
  }
  const ranked = [...scored].sort((a, b) => (b.enough_trades - a.enough_trades) || (b.score - a.score));
  const best = ranked[0];
  const enough = ranked.filter((r) => r.enough_trades).length;
  const ho = res.holdout;
  const part = ho ? (best.train || best) : best;
  append(box, [
    stageTag("past"),
    h("p", { class: "result-lede" }, `From ${day(res.start)} to ${day(res.end)}, ${res.tested.toLocaleString()} combinations were tested in ${res.seconds}s. The best ${scoreName.toLowerCase()}`,
      ho ? ` on the first ${100 - ho.pct}% of the period` : "", " came from ",
      h("strong", {}, label(best.values)), ` (${fmtScore(best.score)}, ${signed(part.return_pct, "%")} over ${part.trades} trades).`),
    res.selection_note ? h("p", { class: "small muted" }, res.selection_note) : null,
    enough < scored.length ? h("p", { class: "muted small" }, `${scored.length - enough} of ${scored.length} combinations made fewer than ${res.min_trades} trades${ho ? " in the ranking part" : ""} and are ranked below the rest: too few trades to trust.`) : null,
    axes.length >= 2 ? heatmap(rows, axes, res, fmtScore) : null,
    ho || res.walk_forward || res.overfitting || res.deflated || res.multiple_testing || res.stability ? stageTag("robust") : null,
    ho ? renderHoldout(ho, best, label) : null,
    res.walk_forward ? renderWalkForwardGrid(res.walk_forward, label) : null,
    res.overfitting ? renderOverfitting(res.overfitting) : null,
    res.deflated ? renderDeflated(res.deflated, "the top combination") : null,
    res.multiple_testing ? renderMultipleTesting(res.multiple_testing) : null,
    res.stability ? renderStability(res.stability, fmtScore) : null,
  ]);
  const shown = ranked.slice(0, 40);
  const table = h("table", { class: "trades sweep-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "#"), axes.map((a) => h("th", {}, a.label)), h("th", { class: "num" }, scoreName), h("th", { class: "num" }, ho ? "Return (ranked part)" : "Return"),
      h("th", { class: "num" }, "Worst drop"), h("th", { class: "num" }, "Trades"), h("th", { class: "num" }, "Profit factor"),
      ho ? h("th", { class: "num" }, "Kept-back return") : null, ho ? h("th", { class: "num" }, "Kept-back trades") : null, onApply ? h("th", {}) : null)),
    h("tbody", {}, shown.map((r, i) => {
      const isNow = axes.every((a, k) => a.current === r.values[k]);
      const p = ho && r.train ? r.train : r;
      return h("tr", { class: (isNow ? "current " : "") + (r.enough_trades ? "" : "muted") },
        h("td", {}, String(i + 1)),
        r.values.map((v, k) => h("td", {}, optionLabel(axes[k], v))),
        h("td", { class: "num" }, fmtScore(r.score), r.enough_trades ? null : h("span", { class: "tag", title: `Fewer than ${res.min_trades} trades` }, "few")),
        h("td", { class: "num " + (p.return_pct >= 0 ? "gain" : "loss") }, signed(p.return_pct, "%")),
        h("td", { class: "num" }, num(p.max_drawdown_pct, 1) + "%"),
        h("td", { class: "num" }, String(p.trades)),
        h("td", { class: "num" }, p.profit_factor === null || p.profit_factor === undefined ? "–" : num(p.profit_factor, 2)),
        ho ? h("td", { class: "num " + ((r.holdout || {}).return_pct >= 0 ? "gain" : "loss") }, r.holdout ? signed(r.holdout.return_pct, "%") : "–") : null,
        ho ? h("td", { class: "num" }, r.holdout ? String(r.holdout.trades) : "–") : null,
        onApply ? h("td", { class: "num" }, isNow ? h("span", { class: "tag" }, "now") : h("button", { type: "button", class: "quiet", onclick: (e) => {
          onApply(axes, r.values);
          e.target.closest("tbody").querySelectorAll("tr").forEach((tr) => tr.classList.remove("current"));
          e.target.closest("tr").classList.add("current");
          e.target.replaceWith(h("span", { class: "tag" }, "now"));
        } }, "Apply settings")) : null);
    })));
  append(box, [
    h("div", { class: "table-wrap" }, table),
    ranked.length > shown.length ? h("p", { class: "small muted" }, `Showing the top ${shown.length} of ${ranked.length}.`) : null,
    res.failed ? h("p", { class: "small muted" }, `${res.failed} combination${res.failed === 1 ? "" : "s"} couldn't be tested (invalid settings).`) : null,
    h("p", { class: "notice" }, "Careful: the best of hundreds of combinations is almost always luckier than it looks, because you picked it after seeing the results. " +
      "Prefer a combination whose neighbours in the grid also did well, then run the luck check on it before trusting it."),
    res.notes && res.notes.length ? h("ul", { class: "warnings" }, res.notes.map((w) => h("li", {}, w))) : null]);
  return box;
}

/** How the winner did on the part of the period it was never ranked on. */
function renderHoldout(ho, best, label) {
  const b = ho.best;
  if (!b) return h("p", { class: "notice" }, "No combination made any trades in the ranking part of the period, so there is nothing to check on the kept-back part.");
  const gap = b.train_return_pct - b.holdout_return_pct;
  let verdict;
  if (!b.holdout_trades) verdict = "It made no trades in the kept-back part, so nothing was learned about it there.";
  else if (b.holdout_return_pct <= 0) verdict = "It lost money on the part it never saw, after making money on the part it was ranked on.";
  else if (gap > Math.max(5, Math.abs(b.train_return_pct) * 0.5)) verdict = "It still made money on the part it never saw, but less. Expect live results nearer the kept-back figure.";
  else verdict = "It made money on both the part it was ranked on and the part it never saw.";
  const hind = ho.hindsight && ho.hindsight.values.join() !== b.values.join() ? ` The combination that did best on the kept-back part alone was ${label(ho.hindsight.values)} (${signed(ho.hindsight.holdout_return_pct, "%")}), but you could only know that with hindsight.` : "";
  return h("div", { class: "walk holdout" }, h("h3", {}, "Kept-back check", infoTip("The last part of the period was not used for ranking. The winner's result there is the closest thing a backtest has to a fresh test.")),
    h("p", {}, `From ${day(ho.from)} to ${day(ho.to)} (the last ${ho.pct}%), the winner made `,
      h("strong", { class: b.holdout_return_pct >= 0 ? "gain" : "loss" }, signed(b.holdout_return_pct, "%")),
      ` over ${b.holdout_trades} trade${b.holdout_trades === 1 ? "" : "s"}, against ${signed(b.train_return_pct, "%")} on the part it was ranked on. ${verdict}${hind}`));
}

/** Probability of backtest overfitting: how often the best combination on half the period finished below the middle on the other half. */
function renderOverfitting(o) {
  const half = o.blocks / 2;
  return h("div", { class: "walk" }, h("h3", {}, "Probability of backtest overfitting", infoTip("Combinatorially symmetric cross-validation (Bailey, Borwein, López de Prado and Zhu, 2015). " +
      "The period is cut into equal stretches, and every way of choosing half of them is one split. In each split the combination with the most profit on the chosen half is compared with all the others on the rest.")),
    h("p", {}, `From ${day(o.from)} to ${day(o.to)}, cut into ${o.blocks} equal stretches, there are ${o.splits} ways to choose ${half} of them. ` +
      `In ${o.below} of the ${o.splits}, the combination with the most profit on the chosen ${half} finished below the middle of all ${o.combinations} combinations on the other ${half}: `,
      h("strong", {}, pctOf(o.probability)), `. In ${o.lost} of them it lost money on the other ${half}.`));
}

const pText = (p) => (p === null || p === undefined ? "–" : p < 0.001 ? "below 0.001" : num(p, 3));

/** How many combinations clear a 5% test that their average trade made money, before and after allowing for how many were tried. */
function renderMultipleTesting(mt) {
  const lvl = `${num(mt.alpha * 100, 0)}%`;
  const c = mt.chosen;
  const chosen = c && c.p !== null && c.p !== undefined
    ? ` For the chosen combination (${c.trades} trades) the p-value is ${pText(c.p)}: ${pText(c.bonferroni)} after the Bonferroni correction and ${pText(c.bh)} after the Benjamini–Hochberg correction.`
    : c ? " The chosen combination had too few trades, or trades with identical results, to test." : "";
  const untested = mt.untestable ? ` ${mt.untestable} combination${mt.untestable === 1 ? "" : "s"} had too few trades or identical results to test and count as not clearing it.` : "";
  return h("div", { class: "walk" }, h("h3", {}, "Allowing for the number of combinations tried", infoTip(
      "Each combination gets a one-sided t-test of whether its average trade return was above zero, using the trades in the ranked part of the period. " +
      "When many combinations are tested, some clear the bar by chance. The Bonferroni correction multiplies each p-value by the number of tests (strict); " +
      "the Benjamini–Hochberg correction (1995) limits the expected share of chance finds among those that clear it. The t-test treats trades as independent, so it is an approximation.")),
    h("p", {}, `From ${day(mt.from)} to ${day(mt.to)}, of ${mt.tests.toLocaleString("en-GB")} combinations, `,
      h("strong", {}, String(mt.raw)), ` cleared the ${lvl} level on their own, `, h("strong", {}, String(mt.bonferroni)), " after the Bonferroni correction and ",
      h("strong", {}, String(mt.bh)), ` after the Benjamini–Hochberg correction.${chosen}${untested}`));
}

/** How the score changes across the chosen combination's neighbours in the grid. */
function renderStability(st, fmtScore) {
  const tip = infoTip("Neighbours are the combinations one step away from the chosen one in the grid on any of the number settings, with option settings held the same. " +
    "A plateau here means the neighbours' middle score is at least 75% of the chosen one's; a peak means it is lower. Neither says how a setting will do on new data.");
  const head = h("h3", {}, "Neighbouring settings", tip);
  if (!st.neighbours) return h("div", { class: "walk" }, head, h("p", { class: "small muted" }, "No neighbouring combinations were tested next to the chosen one."));
  if (!st.scored) return h("div", { class: "walk" }, head, h("p", { class: "small muted" }, `None of its ${st.neighbours} neighbouring combinations could be scored (no trades).`));
  let shape = "";
  if (st.shape) {
    const share = st.share >= 0 ? `${num(st.share * 100, 0)}% of the chosen one's` : "below zero";
    shape = st.shape === "plateau"
      ? ` Their middle score is ${share}: the scores are fairly level around the chosen setting (a plateau).`
      : ` Their middle score is ${share}: the chosen setting stands above its neighbours (a peak).`;
  }
  return h("div", { class: "walk" }, head,
    h("p", {}, `The ${st.scored} neighbouring combination${st.scored === 1 ? "" : "s"} scored from ${fmtScore(st.low)} to ${fmtScore(st.high)} (middle value `,
      h("strong", {}, fmtScore(st.median)), `), against ${fmtScore(st.score)} for the chosen one; ${st.below} of them scored lower.${shape}`));
}

/** Walk-forward over the whole grid: the combination picked before each stretch and how it did. */
function renderWalkForwardGrid(wf, label) {
  const tip = infoTip("Splits the period into stretches. Before each stretch it picks the combination that scored best on all the data so far, then records how it did on the stretch. Every choice only uses the past, like tuning for real would.");
  if (wf.skipped) return h("div", { class: "walk" }, h("h3", {}, "Walk-forward check", tip), h("p", { class: "muted small" }, wf.skipped));
  const gap = wf.hindsight_pct - wf.walk_forward_pct;
  let verdict = "Tuning as you went would have done about as well as picking the best combination with hindsight: the settings are not very sensitive to when you choose them.";
  if (wf.walk_forward_pct <= 0) verdict = "Tuning as you went would have lost money; the positive results above depend on knowing the best combination in advance.";
  else if (gap > Math.max(5, Math.abs(wf.hindsight_pct) * 0.5)) verdict = "Much of the best combination's result came from hindsight. Expect live results closer to the walk-forward figure.";
  return h("div", { class: "walk" }, h("h3", {}, "Walk-forward check", tip),
    h("p", {}, `From ${day(wf.from)} to ${day(wf.to)}, re-picking the best combination before each of ${wf.folds} stretches would have made `,
      h("strong", { class: wf.walk_forward_pct >= 0 ? "gain" : "loss" }, signed(wf.walk_forward_pct, "%")),
      `. Picking the single best combination with hindsight (${label(wf.hindsight_values)}) made ${signed(wf.hindsight_pct, "%")}; the average combination made ${signed(wf.average_pct, "%")}. ${verdict}`),
    wf.selection_note ? h("p", { class: "small muted" }, wf.selection_note) : null,
    h("div", { class: "table-wrap" }, h("table", { class: "trades sweep-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Stretch"), h("th", {}, "Picked using data up to"), h("th", {}, "Combination"), h("th", { class: "num" }, "Result on the stretch"), h("th", { class: "num" }, "Trades"))),
      h("tbody", {}, wf.windows.map((w, i) => h("tr", {}, h("td", {}, String(i + 1)), h("td", {}, day(w.train_to)),
        h("td", {}, w.values ? label(w.values) : h("span", { class: "muted" }, "nothing had traded yet")),
        h("td", { class: "num " + (w.test_return_pct >= 0 ? "gain" : "loss") }, signed(w.test_return_pct, "%")), h("td", { class: "num" }, String(w.test_trades))))))));
}

/** Best score for each pair of values of the first two settings (the best over any third setting). */
function heatmap(rows, axes, res, fmtScore) {
  const xs = [...new Set(rows.map((r) => r.values[0]))];
  const ys = [...new Set(rows.map((r) => r.values[1]))];
  if (xs.length < 2 || ys.length < 2 || xs.length * ys.length > 625) return null;
  const cell = new Map();
  for (const r of rows) {
    if (r.score === null || r.score === undefined) continue;
    const k = `${r.values[0]}|${r.values[1]}`;
    if (!cell.has(k) || r.score > cell.get(k).score) cell.set(k, r);
  }
  const vals = [...cell.values()].map((r) => r.score);
  const hi = Math.max(...vals), lo = Math.min(...vals);
  const span = Math.max(hi, 0) - Math.min(lo, 0) || 1;
  const tone = (v) => {
    const p = Math.round(Math.min(1, Math.abs(v) / span) * 70) + 10;
    return `background:color-mix(in srgb, var(${v >= 0 ? "--gain" : "--loss"}) ${p}%, transparent)`;
  };
  const sz = `width:${Math.max(2.2, Math.min(4, 28 / xs.length))}rem;height:1.6rem;text-align:center;font-size:11px;border:1px solid var(--line-soft)`;
  const table = h("table", { class: "heatmap", style: "border-collapse:collapse;margin:.6rem 0" },
    h("thead", {}, h("tr", {}, h("th", { class: "small muted", style: "text-align:right;padding-right:.4rem" }, `${axes[1].label} ↓ ${axes[0].label} →`),
      xs.map((x) => h("th", { class: "small muted", style: sz }, optionLabel(axes[0], x))))),
    h("tbody", {}, ys.map((y) => h("tr", {}, h("th", { class: "small muted", style: "text-align:right;padding-right:.4rem" }, optionLabel(axes[1], y)),
      xs.map((x) => {
        const r = cell.get(`${x}|${y}`);
        const isBest = r && res.best && r.values[0] === res.best[0] && r.values[1] === res.best[1];
        return h("td", { style: sz + (r ? ";" + tone(r.score) : "") + (isBest ? ";outline:2px solid var(--ink)" : ""),
          title: r ? `${axes[0].label} ${optionLabel(axes[0], x)}, ${axes[1].label} ${optionLabel(axes[1], y)}: ${fmtScore(r.score)}, ${r.trades} trades` : "Not tested" },
          r ? (r.enough_trades ? fmtScore(r.score) : "·") : "");
      })))));
  return h("div", { class: "table-wrap" }, table, h("p", { class: "small muted" },
    `Each cell is the best ${axes.length > 2 ? "over the third setting " : ""}for that pair. A greener cell scored higher; a dot means too few trades. Look for a green area, not a lone bright cell.`));
}

function optionLabel(setting, v) {
  if (setting.type === "select") { const o = setting.options.find((x) => x.value === v); return o ? o.label : String(v); }
  return short(v);
}

function renderSweep(res, setting, onUse) {
  const rows = res.rows;
  const ok = rows.filter((r) => !r.error);
  const box = h("div", { class: "sweep" });
  if (!ok.length) {
    box.appendChild(h("p", { class: "error-box" }, "None of the values could be tested: " + rows[0].error));
    return box;
  }
  const best = ok.reduce((a, b) => (b.return_pct > a.return_pct ? b : a));
  const steady = ok.reduce((a, b) => (b.neighbourhood_pct > a.neighbourhood_pct ? b : a));
  append(box, [
    stageTag("past"),
    h("p", { class: "result-lede" }, `From ${day(res.start)} to ${day(res.end)}, the highest return came from `,
      h("strong", {}, optionLabel(setting, best.value)), ` (${signed(best.return_pct, "%")}).`),
    setting.type === "number" && steady.value !== best.value
      ? h("p", { class: "muted" }, `Taking each value together with its neighbours, `, h("strong", {}, optionLabel(setting, steady.value)),
        ` sits in the strongest area (${signed(steady.neighbourhood_pct, "%")} on average).`) : null,
    sweepChart(ok, setting),
  ]);
  const table = h("table", { class: "trades sweep-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Value"), h("th", { class: "num" }, "Return"), h("th", { class: "num" }, "Worst drop"),
      h("th", { class: "num" }, "Trades"), h("th", { class: "num" }, "Won"), h("th", {}, "Both halves"), onUse ? h("th", {}) : null)),
    h("tbody", {}, rows.map((r) => r.error
      ? h("tr", {}, h("td", {}, optionLabel(setting, r.value)), h("td", { colspan: onUse ? 6 : 5, class: "loss" }, r.error))
      : h("tr", { class: r.value === setting.current ? "current" : null },
        h("td", {}, optionLabel(setting, r.value), r.value === setting.current ? h("span", { class: "tag" }, "now") : null),
        h("td", { class: "num " + (r.return_pct >= 0 ? "gain" : "loss") }, signed(r.return_pct, "%")),
        h("td", { class: "num" }, num(r.max_drawdown_pct, 1) + "%"),
        h("td", { class: "num" }, String(r.trades)),
        h("td", { class: "num" }, r.trades ? num(r.win_rate_pct, 0) + "%" : "–"),
        h("td", {}, r.trades ? (r.first_half > 0 && r.second_half > 0 ? "Profitable in both" : "No") : "–"),
        onUse ? h("td", { class: "num" }, r.value === setting.current ? null : h("button", { type: "button", class: "quiet",
          onclick: (e) => { onUse(r.value); e.target.closest("tbody").querySelectorAll("tr").forEach((tr) => tr.classList.remove("current")); e.target.closest("tr").classList.add("current"); e.target.remove(); } }, "Use")) : null))));
  append(box, [h("div", { class: "table-wrap" }, table)]);
  if (res.walk_forward) append(box, [stageTag("robust"), renderWalkForward(res.walk_forward, setting)]);
  append(box, [
    h("p", { class: "notice" }, "Careful: the value that did best on past prices is usually luckier than it looks, because you picked it after seeing the results. " +
      "Prefer a value whose neighbours also did well, check it was profitable in both halves, then run the luck check on it."),
    res.notes && res.notes.length ? h("ul", { class: "warnings" }, res.notes.map((w) => h("li", {}, w))) : null]);
  return box;
}

function renderWalkForward(wf, setting) {
  const gap = wf.hindsight_pct - wf.walk_forward_pct;
  let verdict;
  if (wf.walk_forward_pct <= 0) verdict = "Tuning as you went would have lost money; the positive results above depend on knowing the best value in advance.";
  else if (gap > Math.max(5, Math.abs(wf.hindsight_pct) * 0.5)) verdict = "Much of the best value's result came from hindsight. Expect live results closer to the walk-forward figure.";
  else verdict = "Choosing from past data held up reasonably well on the data that came after it.";
  return h("div", { class: "walk" }, h("h3", {}, "Walk-forward check", infoTip("Splits the period into stretches. Before each stretch it picks the value that did best on all the data so far, then records how that value did on the stretch. Every choice only uses the past, like tuning for real would.")),
    h("p", {}, `From ${day(wf.from)} to ${day(wf.to)}, picking the best value from the past each time made `,
      h("strong", { class: wf.walk_forward_pct >= 0 ? "gain" : "loss" }, signed(wf.walk_forward_pct, "%")),
      `. With hindsight, the best single value (${optionLabel(setting, wf.hindsight_value)}) made ${signed(wf.hindsight_pct, "%")}; the average of all values ${signed(wf.average_pct, "%")}.`),
    h("p", { class: "small muted" }, verdict),
    wf.selection_note ? h("p", { class: "small muted" }, wf.selection_note) : null,
    h("div", { class: "table-wrap" }, h("table", { class: "trades" },
      h("thead", {}, h("tr", {}, h("th", {}, "Tuned on data up to"), h("th", {}, "Picked"), h("th", { class: "num" }, "Result then"),
        h("th", {}, "Next stretch, to"), h("th", { class: "num" }, "Result"))),
      h("tbody", {}, wf.windows.map((w) => h("tr", {}, h("td", {}, day(w.train_to)), h("td", {}, optionLabel(setting, w.value)),
        h("td", { class: "num" }, signed(w.train_return_pct, "%")), h("td", {}, day(w.test_to)),
        h("td", { class: "num " + (w.test_return_pct >= 0 ? "gain" : "loss") }, signed(w.test_return_pct, "%"))))))));
}

function sweepChart(rows, setting) {
  const W = 560, H = 190, P = 8, B = 26;
  const ys = rows.map((r) => r.return_pct);
  const hi = Math.max(0, ...ys), lo = Math.min(0, ...ys);
  const span = hi - lo || 1;
  const y = (v) => P + ((hi - v) * (H - P - B)) / span;
  const slot = (W - 2 * P) / rows.length;
  const bw = Math.max(4, Math.min(42, slot * 0.66));
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Return for each value" });
  s.appendChild(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 14, class: "chart-bg" }));
  s.appendChild(svg("line", { x1: P, x2: W - P, y1: y(0), y2: y(0), class: "chart-base" }));
  const wrap = h("div", { class: "chart" });
  const readout = h("span", { class: "readout" }, "Return for each value");
  wrap.appendChild(h("div", { class: "chart-title" }, h("h3", {}, "Return by value"), readout));
  rows.forEach((r, i) => {
    const cx = P + slot * (i + 0.5);
    const top = Math.min(y(r.return_pct), y(0));
    const height = Math.max(1.5, Math.abs(y(r.return_pct) - y(0)));
    const bar = svg("rect", { x: cx - bw / 2, y: top, width: bw, height, rx: Math.min(6, bw / 2),
      class: `bar ${r.return_pct >= 0 ? "gain" : "loss"}${r.value === setting.current ? " current" : ""}` });
    bar.style.animationDelay = `${i * 25}ms`;
    bar.addEventListener("pointerenter", () => { readout.textContent = `${optionLabel(setting, r.value)}: ${signed(r.return_pct, "%")}, ${r.trades} trades`; });
    bar.addEventListener("pointerleave", () => { readout.textContent = "Return for each value"; });
    s.appendChild(bar);
    if (rows.length <= 14 || i % Math.ceil(rows.length / 12) === 0) {
      const t = svg("text", { x: cx, y: H - 8, class: "bar-label", "text-anchor": "middle" });
      t.textContent = optionLabel(setting, r.value).slice(0, 10);
      s.appendChild(t);
    }
  });
  wrap.appendChild(s);
  return wrap;
}

function renderLuck(res) {
  const box = h("div", { class: "luck" });
  const sym = res.currency;
  const allPass = res.total && res.passed === res.total;
  append(box, [
    stageTag("robust"),
    h("div", { class: "luck-score" },
      h("div", { class: "luck-ring " + (allPass ? "gain" : res.passed >= res.total - 1 ? "warn" : "loss") },
        h("strong", {}, `${res.passed}/${res.total}`)),
      h("div", {},
        h("p", { class: "result-lede" }, res.profitable
          ? `Passed ${res.passed} of ${res.total} luck checks.`
          : `These rules lost money over this period (${signed(res.return_pct, "%")}), so there's no profit to explain.`),
        h("p", { class: "muted small" }, `${day(res.start)} to ${day(res.end)}, ${res.trades} trades. Took ${res.seconds}s.`))),
    h("ul", { class: "checks" }, res.checks.map((c) => h("li", { class: c.passed === null ? "skip" : c.passed ? "pass" : "fail" },
      h("span", { class: "check-icon", "aria-hidden": "true" }, c.passed === null ? "–" : c.passed ? "✓" : "✕"),
      h("div", {}, h("strong", {}, c.title), h("p", { class: "small muted" }, c.detail),
        c.next_step ? h("p", { class: "small next-step" }, h("strong", {}, "What you can do: "), c.next_step) : null)))),
  ]);
  if (res.random) box.appendChild(randomStrip(res.random));
  if (res.order) {
    const o = res.order;
    box.appendChild(h("p", { class: "small" }, `Same trades in other orders (${o.runs.toLocaleString("en-GB")} reshuffles): the worst drop was typically `,
      h("strong", {}, `${num(Math.abs(o.median_dd), 1)}%`), `, 1 order in 20 saw `, h("strong", {}, `${num(Math.abs(o.bad_dd), 1)}%`),
      ` or worse, and 1 in 20 stayed under ${num(Math.abs(o.good_dd), 1)}%. This backtest's order gave ${num(Math.abs(o.actual_dd), 1)}%. Plan for the bigger number.`));
  }
  if (res.resample) {
    const r = res.resample;
    box.appendChild(h("p", { class: "small muted" }, `Average trade ${signed(r.avg_r, "R")}; across the reshuffled trade sets it ranged from ${signed(r.avg_r_low, "R")} to ${signed(r.avg_r_high, "R")} (middle 90%).`));
  }
  if (res.deflated) box.appendChild(renderDeflated(res.deflated, "these rules"));
  else if (res.trades) box.appendChild(h("p", { class: "small muted" }, "The deflated Sharpe ratio needs at least 10 trades with different results."));
  if (res.regimes) box.appendChild(renderRegimes(res.regimes, sym));
  box.appendChild(h("p", { class: "notice" }, "Passing these checks makes luck a less likely explanation for the backtest. It doesn't mean the rules will make money in future: markets change."));
  if (res.notes && res.notes.length) box.appendChild(h("ul", { class: "warnings" }, res.notes.map((w) => h("li", {}, w))));
  return box;
}

const pctOf = (p) => num(p * 100, 0) + "%";

/** Deflated Sharpe ratio: the chance the true per-trade Sharpe is above what luck alone would give after that many tries. */
function renderDeflated(d, what) {
  const tries = d.trials === 1 ? "1 try" : `${d.trials.toLocaleString("en-GB")} tries`;
  const bar = d.trials === 1 ? "zero" : `${num(d.bar, 3)}, the best you'd expect from ${tries} with no real edge`;
  return h("div", { class: "walk" }, h("h3", {}, "Deflated Sharpe ratio", infoTip("The Sharpe ratio here is per trade: the average trade's return divided by how much trade returns varied. " +
      "The deflated version asks how likely the true ratio is above a bar that rises with the number of tries, allowing for lopsided results such as rare big wins (Bailey and López de Prado, 2014). With 1 try it is the probabilistic Sharpe ratio.")),
    h("p", {}, `Counting ${tries} and ${d.trades} trades, the chance that the true per-trade Sharpe ratio of ${what} is above ${bar} is `,
      h("strong", {}, pctOf(d.probability)), `. Measured per-trade Sharpe ratio: ${num(d.sharpe, 3)}.`));
}

/** Trades by the market's direction and how busy it was on the day each opened. */
function renderRegimes(reg, sym) {
  const tip = infoTip(`Rising: the previous day's close was above its ${reg.trend_days}-day average; falling: below it. ` +
    `Busy: daily price moves over the previous ${reg.vol_days} days were larger than the middle value for this period; calm: smaller. Each trade counts in the conditions of the day it opened.`);
  const groups = [["direction", "Direction"], ["activity", "Activity"]];
  return h("div", { class: "walk regimes" }, h("h3", {}, "By market conditions", tip),
    h("div", { class: "table-wrap" }, h("table", { class: "trades sweep-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Market"), h("th", { class: "num" }, "Share of days"), h("th", { class: "num" }, "Trades"),
        h("th", { class: "num" }, "Won"), h("th", { class: "num" }, "Profit"), h("th", { class: "num" }, "Average trade"))),
      groups.map(([key, label]) => h("tbody", {}, h("tr", { class: "group-row" }, h("th", { colspan: "6", scope: "rowgroup" }, label)),
        reg.rows.filter((r) => r.split === key).map((r) => h("tr", {},
          h("td", {}, r.label), h("td", { class: "num" }, num(r.days_pct, 0) + "%"), h("td", { class: "num" }, String(r.trades)),
          h("td", { class: "num" }, r.win_rate_pct === null ? "–" : num(r.win_rate_pct, 0) + "%"),
          h("td", { class: "num " + (r.pnl >= 0 ? "gain" : "loss") }, r.trades ? money(r.pnl, sym) : "–"),
          h("td", { class: "num" }, r.avg_r === null ? "–" : signed(r.avg_r, "R")))))))),
    reg.unknown ? h("p", { class: "small muted" }, `${reg.unknown} trade${reg.unknown === 1 ? "" : "s"} opened before there was ${reg.trend_days} days of history to tell the market's direction, so ${reg.unknown === 1 ? "it isn't" : "they aren't"} counted by direction.`) : null);
}

function randomStrip(r) {
  const W = 560, H = 92, P = 14;
  const all = [...r.returns, r.real, 0];
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = hi - lo || 1;
  const x = (v) => P + ((v - lo) * (W - 2 * P)) / span;
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Returns on random price paths compared with the real backtest" });
  s.appendChild(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 14, class: "chart-bg" }));
  s.appendChild(svg("line", { x1: x(0), x2: x(0), y1: 10, y2: H - 22, class: "chart-base", "stroke-dasharray": "3 4" }));
  const sorted = r.returns.slice().sort((a, b) => a - b);
  const stack = {};
  sorted.forEach((v, i) => {
    const bucket = Math.round(x(v) / 9);
    const n = (stack[bucket] = (stack[bucket] || 0) + 1);
    const c = svg("circle", { cx: x(v), cy: H - 30 - (n - 1) * 8, r: 3.6, class: "dot-random" });
    c.style.animationDelay = `${i * 12}ms`;
    s.appendChild(c);
  });
  s.appendChild(svg("line", { x1: x(r.real), x2: x(r.real), y1: 8, y2: H - 22, class: "real-line" }));
  const label = svg("text", { x: Math.min(W - P, Math.max(P, x(r.real))), y: H - 8, class: "bar-label strong", "text-anchor": "middle" });
  label.textContent = `Real: ${signed(r.real, "%")}`;
  s.appendChild(label);
  return h("div", { class: "chart" }, h("div", { class: "chart-title" }, h("h3", {}, "Real market vs random markets"),
    h("span", { class: "readout" }, `Random median ${signed(r.median, "%")}` + (r.p5 === undefined ? "" : `, middle 90% ${signed(r.p5, "%")} to ${signed(r.p95, "%")}`))), s);
}

/** In-sample vs out-of-sample figures from a backtest run with a train / test split. */
function renderSplit(res, sym) {
  const a = res.metrics, b = res.oos_metrics;
  const pct = (v, d = 1) => (v === null || v === undefined ? "–" : num(v, d) + "%");
  const lines = [
    ["Period", `${day(a.start)} to ${day(a.end)}`, `${day(b.start)} to ${day(b.end)}`],
    ["Return", signed(a.total_return_pct, "%"), signed(b.total_return_pct, "%"), "total_return_pct"],
    ["Sharpe (yearly)", num(a.sharpe), num(b.sharpe)],
    ["Worst drawdown", pct(a.max_drawdown_pct), pct(b.max_drawdown_pct)],
    ["Won", a.trades ? pct(a.win_rate_pct, 0) : "–", b.trades ? pct(b.win_rate_pct, 0) : "–"],
    ["Trades", String(a.trades), String(b.trades)],
  ];
  const tone = (v) => (v === null || v === undefined ? "" : v >= 0 ? "gain" : "loss");
  const table = h("table", { class: "split-table" },
    h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", { class: "num" }, `In-sample (${num(a.split_pct, 0)}%)`), h("th", { class: "num" }, `Out-of-sample (${num(100 - a.split_pct, 0)}%)`))),
    h("tbody", {}, lines.map(([k, x, y, key]) => h("tr", {}, h("th", { scope: "row" }, k),
      h("td", { class: "num " + (key ? tone(a[key]) : "") }, x), h("td", { class: "num " + (key ? tone(b[key]) : "") }, y)))));
  const gap = a.total_return_pct - b.total_return_pct;
  let note = `Out-of-sample starts ${day(res.split_date)} with a fresh balance of ${money(b.start_equity, sym)}. The rest of this page shows the in-sample part only.`;
  if (!b.trades) note += " No trades were made out-of-sample.";
  else if (b.total_return_pct < a.total_return_pct) note += ` The out-of-sample return is ${num(gap, 1)} percentage points lower than in-sample.`;
  const box = h("div", { class: "walk split" }, h("h3", {}, "In-sample vs out-of-sample",
    infoTip("The in-sample part is what you would tune your rules on. The out-of-sample part was not looked at while doing that, so it is closer to how the rules do on prices they haven't seen.")),
    table, h("p", { class: "small" }, note));
  if (res.oos_equity && res.oos_equity.length)
    box.appendChild(lineChart(res.oos_equity, { title: "Balance, out-of-sample part", fmt: (v) => money(v, sym), baseline: b.start_equity }));
  return box;
}

export function renderBacktest(res) {
  const m = res.metrics;
  const sym = res.currency;
  const box = h("div", {}, stageTag("past"));
  const ret = m.total_return_pct;
  if (m.sample_warning) box.appendChild(h("p", { class: "sample-warning", role: "note" }, m.sample_warning));
  append(box, [h("div", { class: "headline" },
    h("div", {}, h("div", { class: "big " + (ret >= 0 ? "gain" : "loss") }, signed(ret, "%")), h("div", { class: "lbl" }, "Return")),
    h("div", {}, h("div", { class: "big" }, num(m.max_drawdown_pct, 1) + "%"), h("div", { class: "lbl" }, "Worst drawdown")),
    h("div", {}, h("div", { class: "big" }, String(m.trades)), h("div", { class: "lbl" }, "Trades")),
    m.trades ? h("div", {}, h("div", { class: "big" }, num(m.win_rate_pct, 0) + "%"), h("div", { class: "lbl" }, "Won")) : null)]);

  const rows = [
    ["Period", `${day(m.start)} to ${day(m.end)}`],
    ["Balance", `${money(m.start_equity, sym)} to ${money(m.end_equity, sym)}`],
    ["Buy and hold, same period", signed(m.buy_and_hold_pct, "%")],
    ["Time in a trade", num(m.time_in_market_pct, 1) + "%"],
  ];
  if (m.trades) rows.push(
    ["Average win", money(m.avg_win, sym)], ["Average loss", money(m.avg_loss, sym)],
    ["Average trade", `${money(m.expectancy, sym)} (${signed(m.avg_r, "R")})`],
    ["Profit factor", m.profit_factor === null ? "No losses" : num(m.profit_factor)],
    ["Largest win / loss", `${money(m.largest_win, sym)} / ${money(m.largest_loss, sym)}`],
    ["Longest winning / losing run", `${m.longest_winning_streak} / ${m.longest_losing_streak}`],
    ["Candles held on average", `${num(m.avg_bars_held, 1)} (${hours(m.avg_hold_hours)})`],
    ["Sharpe (yearly)", num(m.sharpe) + (m.sharpe_ci ? ` (95% CI, approx. assuming independent trades: ${num(m.sharpe_ci[0])} to ${num(m.sharpe_ci[1])})` : "")],
    ["Sortino (yearly)", num(m.sortino)],
    ...(m.ratio_ranges ? [["Sharpe and Sortino, 95% range for linked returns", linkedText(m.ratio_ranges)]] : []),
    ...(m.win_rate_range ? [["Share of trades won, 95% range", `${num(m.win_rate_range[0], 0)}% to ${num(m.win_rate_range[1], 0)}% (Wilson range from ${m.trades} trades)`]] : []),
    ["Longest time below a previous high", `${num(m.longest_underwater_days, 1)} days`],
    ["Yearly growth rate (CAGR)", m.cagr_pct === null || m.cagr_pct === undefined ? "Needs a test of a year or more" : signed(m.cagr_pct, "%")],
    ["Calmar (yearly growth ÷ worst drawdown)", num(m.calmar)],
    ["Worst drawdown, how long", drawdownLength(m.worst_drawdown)],
    ["Average of the worst 5% of days", m.expected_shortfall_pct === null || m.expected_shortfall_pct === undefined
      ? "Needs 20 days or more" : signed(m.expected_shortfall_pct, "%")],
    ["How trades ended", Object.entries(m.exit_reasons || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ")],
    ["Spread paid", money(m.total_spread_cost || 0, sym) + (m.used_data_spread ? " (real bid/ask)" : "")],
    ["Overnight funding paid", money(m.total_funding || 0, sym)],
    ...(m.total_borrow ? [["Borrow fee paid (shorts)", money(m.total_borrow, sym)]] : []),
    ...(m.total_commission ? [["Commission paid", money(m.total_commission, sym)]] : []),
    ...(m.total_slippage ? [["Slippage paid", money(m.total_slippage, sym)]] : []),
    ["Stop or target first?", m.used_finer_candles ? "Checked with finer candles" : "Stop assumed first"],
  );
  if (res.oos_metrics) box.appendChild(renderSplit(res, sym));
  append(box, [h("dl", { class: "metrics" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))]);
  append(box, [h("ul", { class: "warnings" }, res.warnings.map((w) => h("li", {}, w)))]);
  if (res.why_no_trades) box.appendChild(renderWhyNoTrades(res.why_no_trades));
  // balance, with buy-and-hold over the same period for comparison
  let equityNode = null;
  const drawEquity = () => {
    const show = res.benchmark && res.benchmark.length && pref("ta-show-bh", "") === "1";
    const node = lineChart(res.equity, { title: "Balance", fmt: (v) => money(v, sym), baseline: m.start_equity,
      compare: show ? res.benchmark : null, compareLabel: "Buy and hold",
      onToggleCompare: res.benchmark && res.benchmark.length ? (on) => { savePref("ta-show-bh", on ? "1" : ""); drawEquity(); } : null,
      compareOn: show });
    if (equityNode) equityNode.replaceWith(node); else box.appendChild(node);
    equityNode = node;
  };
  drawEquity();
  // price chart: click a trade (row or marker) to zoom in on it
  let chart = null, details = null, stepper = null;
  const rowsById = [];
  const showTrade = (k) => {
    if (!chart || k < 0 || k >= res.trades.length) return;
    const t = res.trades[k];
    details.open = true;
    chart.focus(t);
    rowsById.forEach((tr, i) => tr.classList.toggle("selected", i === k));
    clear(stepper);
    const hold = `held ${t.bars_held} candle${t.bars_held === 1 ? "" : "s"}`;
    const exc = t.mae_r !== undefined ? ` · worst ${signed(t.mae_r, "R", 1)}, best ${signed(t.mfe_r, "R", 1)}` : "";
    const why = t.entry_reason && Object.keys(t.entry_reason).length
      ? " · at entry " + Object.entries(t.entry_reason).map(([k2, v]) => `${k2} ${v === null ? "–" : num(v)}`).join(", ") : "";
    append(stepper, [
      h("button", { type: "button", class: "small", disabled: k === 0, onclick: () => showTrade(k - 1), "aria-label": "Previous trade" }, "‹ Prev"),
      h("span", { class: "small" }, h("strong", {}, `Trade ${k + 1} of ${res.trades.length}`), ` · `,
        h("span", { class: t.pnl >= 0 ? "gain" : "loss" }, `${money(t.pnl, sym)} (${signed(t.r_multiple, "R", 1)})`), ` · ${hold}${exc}${why}`),
      h("button", { type: "button", class: "small", disabled: k === res.trades.length - 1, onclick: () => showTrade(k + 1), "aria-label": "Next trade" }, "Next ›"),
      h("button", { type: "button", class: "quiet small", onclick: () => { chart.clearFocus(); clear(stepper); rowsById.forEach((tr) => tr.classList.remove("selected")); } }, "Show all"),
    ]);
    details.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  if (res.chart && res.chart.candles.length) {
    chart = candleChart(res.chart, { height: 320, onMark: (mk) => showTrade(mk.trade) });
    stepper = h("div", { class: "trade-stepper inline" });
    details = h("details", { class: "chart price-details" },
      h("summary", {}, res.trades.length ? "Price chart with trades (click a trade to zoom in on it)" : "Price chart"), stepper, chart);
    append(box, [details]);
  }
  append(box, [lineChart(res.drawdown, { title: "Drawdown", fmt: (v) => num(v, 1) + "%", baseline: 0, tone: "loss", fill: true })]);
  if (res.monte_carlo) box.appendChild(renderMonteCarlo(res.monte_carlo));
  if (m.monthly_returns && m.monthly_returns.length) box.appendChild(monthlyGrid(m.monthly_returns));
  if (m.trades) box.appendChild(renderBreakdowns(m, sym));
  if (m.trades) {
    append(box, [h("div", { class: "chart-pair" },
      histogram(m.r_histogram, { title: "Trades by result (R)", fmt: (v) => signed(v, "R", 1), unit: "R" }),
      histogram(m.hold_histogram, { title: "Trades by time held", fmt: (v) => hours(v), unit: "" }))]);
  }
  if (res.trades.length) {
    const t = res.trades.slice().reverse();
    append(box, [h("details", { class: "chart" }, h("summary", {}, `Trades (${res.trade_count})`),
      h("div", { class: "table-wrap" }, h("table", { class: "trades" },
        h("thead", {}, h("tr", {}, ["Opened", "Entry", "Closed", "Exit", "Size", "Reason"].map((x) => h("th", {}, x)),
          h("th", { class: "num" }, "P/L"), h("th", { class: "num" }, "R"))),
        h("tbody", {}, t.map((x, ri) => {
          const k = t.length - 1 - ri;  // newest first in the table; k is the index in res.trades
          const tr = h("tr", { class: chart ? "clickable" : "", tabindex: chart ? "0" : null, title: chart ? "Show this trade on the chart" : null,
            onclick: () => showTrade(k), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showTrade(k); } } },
          h("td", {}, when(x.entry_time)), h("td", {}, num(x.entry_price)), h("td", {}, when(x.exit_time)),
          h("td", {}, num(x.exit_price)), h("td", {}, short(x.size)), h("td", {}, x.exit_reason.replace(/_/g, " ")),
          h("td", { class: "num " + (x.pnl >= 0 ? "gain" : "loss") }, money(x.pnl, sym)),
          h("td", { class: "num" }, signed(x.r_multiple, "", 2)));
          rowsById[k] = tr;
          return tr;
        })))))]);
  }
  if (res.meta) box.appendChild(testStamp(res.meta, sym, res.data_quality));
  return box;
}

function hours(hrs) {
  if (hrs === null || hrs === undefined) return "–";
  if (hrs < 48) return `${num(hrs, 1)} h`;
  return `${num(hrs / 24, 1)} days`;
}

/** Sharpe and Sortino ranges from the block bootstrap, which keeps linked returns together. */
function linkedText(rr) {
  const band = (b) => (b ? `${num(b[0])} to ${num(b[1])}` : "–");
  const overlap = rr.overlapping_trades
    ? ` ${rr.overlapping_trades} trade${rr.overlapping_trades === 1 ? "" : "s"} opened while another was still open, so their results are linked.` : "";
  return `Sharpe ${band(rr.sharpe)}, Sortino ${band(rr.sortino)}. Resampled ${rr.runs.toLocaleString("en-GB")} times in runs of ${rr.block} candles in a row, ` +
    `so the candles of one trade, and trades that overlap, stay together instead of being treated as independent.${overlap}`;
}

/** Trades reshuffled into other orders: how deep the worst drop could have been with the same trades. */
function renderMonteCarlo(mc) {
  const W = 560, H = 150, P = 8, B = 26;
  const bars = mc.histogram || [];
  const top = Math.max(1, ...bars.map((b) => b.count));
  const slot = (W - 2 * P) / Math.max(1, bars.length);
  const bw = Math.max(4, Math.min(42, slot * 0.72));
  const y = (c) => P + ((top - c) * (H - P - B)) / top;
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Worst drawdown across reshuffled trade orders" });
  s.appendChild(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 14, class: "chart-bg" }));
  const readout = h("span", { class: "readout" }, `${mc.runs.toLocaleString("en-GB")} reshuffles`);
  bars.forEach((b, i) => {
    const cx = P + slot * (i + 0.5);
    const bar = svg("rect", { x: cx - bw / 2, y: y(b.count), width: bw, height: Math.max(1, H - B - y(b.count)), rx: Math.min(6, bw / 2), class: "bar loss" });
    bar.style.animationDelay = `${i * 25}ms`;
    bar.addEventListener("pointerenter", () => { readout.textContent = `${num(b.from, 0)}% to ${num(b.to, 0)}%: ${b.count} of ${mc.runs} orders`; });
    bar.addEventListener("pointerleave", () => { readout.textContent = `${mc.runs.toLocaleString("en-GB")} reshuffles`; });
    s.appendChild(bar);
    if (bars.length <= 14 || i % 2 === 0) {
      const t = svg("text", { x: cx, y: H - 8, class: "bar-label", "text-anchor": "middle" });
      t.textContent = `${num(b.to, 0)}%`;
      s.appendChild(t);
    }
  });
  const stat = (label, v) => h("div", {}, h("div", { class: "big" }, `${num(Math.abs(v), 1)}%`), h("div", { class: "lbl" }, label));
  return h("div", { class: "chart monte-carlo" },
    h("div", { class: "chart-title" }, h("h3", {}, "Same trades, other orders"), readout),
    h("p", { class: "small muted" }, "The backtest's trades shuffled into random orders. The worst drop depends a lot on which trades happen to come together, so plan for the bad case, not this backtest's own order."),
    h("div", { class: "headline compact" }, stat("Mildest 1 in 20", mc.p95), stat("Typical (median)", mc.p50), stat("Worst 1 in 20", mc.p5), stat("This backtest's order", mc.actual_dd)),
    s);
}

/** Years × months grid of balance changes, with a year-to-date column. */
function monthlyGrid(years) {
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const cell = (v) => h("td", { class: "num " + (v === null ? "empty" : v > 0 ? "gain" : v < 0 ? "loss" : "") }, v === null ? "" : signed(v, "%", 1));
  return h("details", { class: "chart", open: true }, h("summary", {}, "Month by month"),
    h("div", { class: "table-wrap" }, h("table", { class: "monthly" },
      h("thead", {}, h("tr", {}, h("th", {}, "Year"), MONTHS.map((x) => h("th", { class: "num" }, x)), h("th", { class: "num ytd" }, "Year"))),
      h("tbody", {}, years.map((y) => h("tr", {}, h("th", {}, String(y.year)), y.months.map(cell), h("td", { class: "num ytd " + (y.ytd > 0 ? "gain" : y.ytd < 0 ? "loss" : "") }, signed(y.ytd, "%", 1))))))),
    h("p", { class: "small muted" }, "Change in the balance over each calendar month of the test. Blank months are outside the test period."));
}

/** "12 days falling, 30 days to get back" for the deepest drop. */
function drawdownLength(w) {
  if (!w) return "No drawdown";
  const back = w.recovered ? `${num(w.days_to_recover, 0)} days to get back` : "not back to the earlier high by the end";
  return `${num(w.days_down, 0)} days falling (from ${day(w.peak)}), ${back}`;
}

/** Biggest falls and rises per day/week/month, and trades grouped by weekday and session. */
function renderBreakdowns(m, sym) {
  const out = h("div", {});
  const periods = [["day", "Day"], ["week", "Week (Mon–Sun)"], ["month", "Month"]].filter(([k]) => m.periods && m.periods[k]);
  const at = (k, iso) => (k === "month" ? new Date(iso).toLocaleDateString("en-GB", { month: "short", year: "numeric" }) : day(iso));
  const tone = (v) => (v > 0 ? "gain" : v < 0 ? "loss" : "");
  if (periods.length) out.appendChild(h("details", { class: "chart" }, h("summary", {}, "Biggest falls and rises"),
    h("div", { class: "table-wrap" }, h("table", { class: "trades" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", { class: "num" }, "Biggest fall"), h("th", { class: "num" }, "Biggest rise"))),
      h("tbody", {}, periods.map(([k, label]) => {
        const p = m.periods[k];
        return h("tr", {}, h("th", { scope: "row" }, label),
          h("td", { class: "num " + tone(p.worst) }, `${signed(p.worst, "%")} (${at(k, p.worst_at)})`),
          h("td", { class: "num " + tone(p.best) }, `${signed(p.best, "%")} (${at(k, p.best_at)})`));
      })))),
    h("p", { class: "small muted" }, "Change in the balance within a single calendar day, week and month of the test.")));
  const table = (title, rows, note) => h("div", {}, h("h3", {}, title),
    h("div", { class: "table-wrap" }, h("table", { class: "trades" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), ["Trades", "Won", "Profit / loss", "Average R"].map((x) => h("th", { class: "num" }, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("th", { scope: "row" }, r.name), h("td", { class: "num" }, String(r.trades)),
        h("td", { class: "num" }, num(r.won_pct, 0) + "%"), h("td", { class: "num " + tone(r.pnl) }, money(r.pnl, sym)),
        h("td", { class: "num" }, signed(r.avg_r, "R"))))))),
    note ? h("p", { class: "small muted" }, note) : null);
  const groups = [];
  if (m.by_weekday && m.by_weekday.length) groups.push(table("By weekday opened (UTC)", m.by_weekday));
  if (m.by_session && m.by_session.length) groups.push(table("By session opened", m.by_session,
    "Asia is Tokyo 09:00–18:00, London 08:00–16:30 London time, New York 08:00–17:00 New York time, each on its own clock so summer time is followed. " +
    "Trades opened while London and New York are both open count as London + New York; weekends and the rest are Other hours."));
  if (groups.length) out.appendChild(h("details", { class: "chart" }, h("summary", {}, "Trades by weekday and session"), ...groups,
    h("p", { class: "small muted" }, "Each trade counts once, under the time it opened. Small groups can swing a lot from a few trades.")));
  return out;
}

/** Bar chart of bucket counts ({from, to, count}), e.g. trades by R or by time held. */
function histogram(buckets, { title, fmt, unit = "" }) {
  const W = 560, H = 170, P = 8, B = 26;
  let bars = buckets || [];
  while (bars.length > 1 && !bars[0].count) bars = bars.slice(1);  // drop empty tails, keep the middle
  while (bars.length > 1 && !bars[bars.length - 1].count) bars = bars.slice(0, -1);
  const top = Math.max(1, ...bars.map((b) => b.count));
  const slot = (W - 2 * P) / Math.max(1, bars.length);
  const bw = Math.max(4, Math.min(42, slot * 0.72));
  const y = (c) => P + ((top - c) * (H - P - B)) / top;
  const label = (b) => (b.from === null ? `< ${fmt(b.to)}` : b.to === null ? `> ${fmt(b.from)}` : `${fmt(b.from)} to ${fmt(b.to)}`);
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
  s.appendChild(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 14, class: "chart-bg" }));
  const total = bars.reduce((a, b) => a + b.count, 0);
  const readout = h("span", { class: "readout" }, `${total} trades`);
  bars.forEach((b, i) => {
    const cx = P + slot * (i + 0.5);
    const tone = unit !== "R" ? "ink" : b.to !== null && b.to <= 0 ? "loss" : "gain";
    const bar = svg("rect", { x: cx - bw / 2, y: y(b.count), width: bw, height: Math.max(1, H - B - y(b.count)), rx: Math.min(6, bw / 2), class: `bar ${tone}` });
    bar.style.animationDelay = `${i * 25}ms`;
    bar.addEventListener("pointerenter", () => { readout.textContent = `${label(b)}: ${b.count} trade${b.count === 1 ? "" : "s"}`; });
    bar.addEventListener("pointerleave", () => { readout.textContent = `${total} trades`; });
    s.appendChild(bar);
    const t = svg("text", { x: cx, y: H - 8, class: "bar-label", "text-anchor": "middle" });
    t.textContent = b.to === null ? `> ${fmt(b.from)}` : fmt(b.to);
    s.appendChild(t);
  });
  return h("div", { class: "chart" }, h("div", { class: "chart-title" }, h("h3", {}, title), readout), s);
}

/** Zero trades: how often each part of the entry rule held, so the blocking part stands out. */
function renderWhyNoTrades(why) {
  const rows = why.conditions || [];
  const leaves = rows.filter((r) => r.kind === "leaf");
  const least = leaves.length ? Math.min(...leaves.map((r) => (r.matched === null ? Infinity : r.matched))) : null;
  const pct = (r) => (r.matched === null || !r.candles ? "–" : `${num((r.matched / r.candles) * 100, 1)}%`);
  const list = h("ol", { class: "conditions" }, rows.map((r) => {
    const lowest = r.kind === "leaf" && r.matched === least;
    const name = r.kind === "leaf" ? renderParts(describeCondition(r.condition), "sentence small")
      : h("p", { class: "sentence small muted" }, r.kind === "all" ? "all of these together" : r.kind === "any" ? "any of these" : "not");
    return h("li", { class: (lowest ? "lowest " : "") + `depth-${Math.min(r.depth, 4)}` }, name,
      h("span", { class: "count num" }, r.matched === null ? "couldn't be checked alone" : `${r.matched.toLocaleString("en-GB")} of ${r.candles.toLocaleString("en-GB")} candles (${pct(r)})`));
  }));
  const blocked = leaves.filter((r) => r.matched === 0);
  const lede = blocked.length
    ? `${blocked.length === 1 ? "One part" : `${blocked.length} parts`} of the entry rule never held on any candle in the test period.`
    : rows.length && rows[0].matched === 0 && leaves.length > 1
      ? "Each part held on some candles, but never all on the same candle."
      : "The entry rule held on some candles; trades may have been skipped (see the notes above).";
  return h("div", { class: "chart why-no-trades" },
    h("div", { class: "chart-title" }, h("h3", {}, "Why didn't it trade?"), h("span", { class: "readout" }, `${why.candles.toLocaleString("en-GB")} candles checked`)),
    h("p", { class: "small" }, lede, " The counts show how often each part held on its own."), list);
}

/** The loss limits that were set, and how many entries they stopped. */
function lossLimits(r, blocked) {
  const set = [r.max_daily_loss_pct != null ? `${short(r.max_daily_loss_pct)}% a day` : "",
    r.max_weekly_loss_pct != null ? `${short(r.max_weekly_loss_pct)}% a week` : "",
    r.max_drawdown_pct != null ? `${short(r.max_drawdown_pct)}% from the high` : "",
    r.max_margin_pct != null ? `margin up to ${short(r.max_margin_pct)}% of the balance` : ""].filter(Boolean);
  if (!set.length) return "None";
  return `${set.join(", ")}; ${blocked || 0} trade${blocked === 1 ? "" : "s"} stopped from opening`;
}

/** Which currency sizes were in, and how they were converted into the account's. */
function currencyText(r, fx) {
  if (r.size_currency !== "market" || !fx) return `Sizes and results in ${r.currency}`;
  if (fx.fixed) return `Sizes in ${fx.from}, converted to ${fx.to} at a fixed ${short(fx.fixed)}`;
  const range = fx.low != null ? ` (${short(+fx.low.toFixed(4))} to ${short(+fx.high.toFixed(4))})` : "";
  return `Sizes in ${fx.from}, converted to ${fx.to} at each day's exchange rate${range}`;
}

/** The margin rate, the most margin any trade needed and any close-outs. */
function marginText(r, meta) {
  if (r.margin_pct == null) return "Not known for this market";
  const parts = [`${short(r.margin_pct)}% of each trade's value`];
  if (meta.max_margin_used_pct != null) parts.push(`at most ${short(+meta.max_margin_used_pct.toFixed(1))}% of the balance held`);
  if (r.close_out_pct != null) parts.push(`close-out below ${short(r.close_out_pct)}% of margin, ${meta.closed_out || 0} trade${meta.closed_out === 1 ? "" : "s"} closed out`);
  return parts.join("; ");
}

/** Plain facts about the price source itself (price definition, adjusted or not, spread
 * availability, usual history coverage), independent of this one backtest's settings. */
function providerFactsList(pf) {
  if (!pf) return null;
  const spread = pf.spread_available === true ? "Available" : pf.spread_available === false ? "Not available"
    : "Depends on the file";
  return h("ul", { class: "small" }, [
    h("li", {}, `Price: ${pf.price}`),
    h("li", {}, pf.adjusted),
    h("li", {}, `Spread data: ${spread}`),
    h("li", {}, `Typical history available: ${pf.history}`),
  ]);
}

/** What exactly was tested, so the same backtest can be run again and compared. */
function testStamp(meta, sym, quality) {
  const rules = meta.version ? `Version ${meta.version} of this strategy` : "Unsaved changes (not a saved version)";
  const spread = meta.used_data_spread ? "Real bid/ask from the price data" : `${short(meta.spread)} price points`;
  const r = meta.risk || {};
  const issues = (quality && quality.issues) || [];
  const session = meta.session;
  const rows = [
    ["Rules", h("span", {}, rules, " ", h("span", { class: "muted" }, `(fingerprint ${meta.rules_hash})`))],
    ...manifestRows(meta),
    ["Market", `${meta.market} (${meta.symbol})`],
    ["Price data", meta.prices ? `${meta.source}. ${meta.prices}` : meta.source],
    ...(meta.provider_facts ? [["Data source details", providerFactsList(meta.provider_facts)]] : []),
    ...(session ? [["Trading hours assumed", `${session.hours}. ${session.weekend}. Worked out from `
      + `${session.calendar_text}.`]] : []),
    ["Candle size", meta.timeframe],
    ["Period", `${day(meta.start)} to ${day(meta.end)}`],
    ["Warm-up before the period", meta.warmup_candles ? `${meta.warmup_candles} candles` : "None (the data starts here)"],
    ["Spread", spread],
    ["Overnight funding", meta.funding_pct ? `${short(meta.funding_pct)}% a year` : "None"],
    ["Slippage per fill", [meta.slippage ? `${short(meta.slippage)} price points` : "", meta.slippage_pct ? `${short(meta.slippage_pct)}% of price` : "",
      meta.slippage_atr_pct ? `${short(meta.slippage_atr_pct)}% of the average candle range` : ""].filter(Boolean).join(" + ") || "None"],
    ["Commission per fill", [meta.commission ? money(meta.commission, sym) : "", meta.commission_pct ? `${short(meta.commission_pct)}% of value` : ""]
      .filter(Boolean).join(" + ") || "None"],
    ["Borrow fee (shorts)", meta.borrow_pct ? `${short(meta.borrow_pct)}% a year` : "None"],
    ...(meta.risk ? [["Sizing", `${money(r.account_size, sym)} account, ${short(r.risk_per_trade_pct)}% risk a trade, ` +
      `${short(r.point_value)} per point, steps of ${short(r.size_step)}${r.compound ? ", compounding" : ", fixed account size"}`]] : []),
    ...(meta.risk ? [["Currency", currencyText(r, meta.fx)], ["Margin", marginText(r, meta)]] : []),
    ...(meta.risk ? [["Trade limits", lossLimits(r, meta.blocked_by_limits)]] : []),
    ...(meta.split_pct ? [["In-sample part", `First ${short(meta.split_pct)}% of the period`]] : []),
    ...(meta.data_fingerprint ? [["Price data fingerprint", `${meta.data_fingerprint} (${(meta.data_candles || 0).toLocaleString("en-GB")} candles, warm-up included)`]] : []),
    ...(quality ? [["Price data checks", issues.length ? h("ul", { class: "small" }, issues.map((i) => h("li", {}, i.text)))
      : `Nothing unusual found in ${(quality.candles || 0).toLocaleString("en-GB")} candles`]] : []),
    ["Tested", `${when(meta.tested_at)}, Altius ${meta.engine_version}`],
  ];
  return h("details", { class: "chart stamp" }, h("summary", {}, "How this was tested"),
    h("dl", { class: "metrics" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
    h("div", { class: "actions" }, h("button", { type: "button", class: "quiet", onclick: () => downloadRecord(meta) }, "Download run record"),
      h("span", { class: "small muted" }, " Everything above as a JSON file, including the rules, to keep or run again later.")));
}

/** Run-record rows from the reproducibility fields: strategy, indicator code versions, period asked, time zones. */
function manifestRows(meta) {
  const rows = [];
  if (meta.strategy_name) rows.push(["Strategy", meta.strategy_name]);
  if (meta.indicators) {
    rows.push(["Indicators", meta.indicators.length
      ? h("span", {}, meta.indicators.map((i) => i.name).join(", "), " ",
        h("span", { class: "muted" }, `(code versions ${meta.indicators.map((i) => i.version).join(", ")})`))
      : "None"]);
  }
  const p = meta.requested_period;
  if (p) {
    rows.push(["Period asked for", p.years ? `Last ${short(p.years)} year${p.years === 1 ? "" : "s"}`
      : p.start || p.end ? `${p.start ? day(p.start) : "the first candle"} to ${p.end ? day(p.end) : "the latest candle"}` : "All the price data loaded"]);
  }
  const tz = meta.timezone;
  if (tz) {
    rows.push(["Time zones", `Candle times in ${tz.prices}; market time zone ${tz.market} (days for loss limits, CSV times without a zone); ` +
      `daily candles end at ${tz.candle_alignment === "utc" ? "midnight UTC" : "5pm New York"}`]);
  }
  if (meta.libraries) rows.push(["Software", `Python ${meta.libraries.python}, pandas ${meta.libraries.pandas}, numpy ${meta.libraries.numpy}`]);
  return rows;
}

/** Save the run record as a JSON file. */
function downloadRecord(meta) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(meta, null, 2)], { type: "application/json" }));
  const name = String(meta.strategy_name || "backtest").replace(/[^\w-]+/g, "-").slice(0, 40);
  const a = h("a", { href: url, download: `run-record-${name}-${String(meta.tested_at || "").slice(0, 10)}.json` });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Line chart of [isoTime, value] points. With timeX the x axis follows the clock (forward tests),
 * otherwise each point gets equal room (backtests skip nights and weekends that way).
 * band: optional [[isoTime, p10, p25, p50, p75, p90], ...] drawn behind the line.
 */
let gradSeq = 0;

/** 3-4 horizontal gridlines at round values, labelled on the right, behind the line. */
export function gridlines(s, lo, hi, y, fmt, W, P) {
  const span = hi - lo;
  if (!(span > 0) || !isFinite(span)) return;
  const rough = span / 3.5;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= rough) || mag * 10;
  const inset = P + 10;  // keep labels clear of the rounded top and bottom edges
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    const yy = y(v);
    if (yy < inset || yy > s.viewBox.baseVal.height - inset) continue;
    s.appendChild(svg("line", { x1: P, x2: W - P, y1: yy.toFixed(1), y2: yy.toFixed(1), class: "chart-grid" }));
    const t = svg("text", { x: W - P - 4, y: (yy - 3).toFixed(1), "text-anchor": "end", class: "chart-grid-label" });
    t.textContent = fmt(v);
    s.appendChild(t);
  }
}

export function lineChart(points, { title, fmt, baseline = null, tone = "ink", fill = true, height = 170, timeX = false, band = null,
  compare = null, compareLabel = "Comparison", onToggleCompare = null, compareOn = false }) {
  const W = 560, H = height, P = 6;
  const wrap = h("div", { class: "chart" });
  const readout = h("span", { class: "readout" });
  const toggle = onToggleCompare ? h("label", { class: "check small compare-toggle" },
    h("input", { type: "checkbox", checked: compareOn || null, onchange: (e) => onToggleCompare(e.target.checked) }), h("span", {}, `Show ${compareLabel.toLowerCase()}`)) : null;
  append(wrap, [h("div", { class: "chart-title" }, h("h3", {}, title), h("span", { class: "chart-tools" }, toggle, readout, title ? expandable(wrap) : null))]);
  if (!points || !points.length) { wrap.appendChild(h("p", { class: "muted small" }, "Not enough data to chart.")); return wrap; }
  if (points.length === 1) points = [points[0], [new Date().toISOString(), points[0][1]]];
  const ts = points.map((p) => new Date(p[0]).getTime());
  const bandRows = band && band.length > 1 ? band : null;
  const ys = points.map((p) => p[1]);
  if (bandRows) for (const b of bandRows) ys.push(b[1], b[5]);
  if (compare) for (const c of compare) ys.push(c[1]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (baseline !== null) { lo = Math.min(lo, baseline); hi = Math.max(hi, baseline); }
  if (hi === lo) { const pad = Math.abs(hi) * 0.002 || 1; hi += pad; lo -= pad; }
  const pad = (hi - lo) * 0.06;
  hi += pad; lo -= pad;
  const t0 = ts[0], t1 = Math.max(ts[ts.length - 1], t0 + 1);
  const xt = (t) => P + ((t - t0) * (W - 2 * P)) / (t1 - t0);
  const x = (i) => (timeX ? xt(ts[i]) : P + (i * (W - 2 * P)) / (points.length - 1));
  const y = (v) => P + ((hi - v) * (H - 2 * P)) / (hi - lo);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const first = points[0][1], last = points[points.length - 1][1];
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `${title || "Balance"} chart: from ${fmt(first)} to ${fmt(last)}, between ${fmt(Math.min(...points.map((p) => p[1])))} and ${fmt(Math.max(...points.map((p) => p[1])))}` });
  s.appendChild(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 14, class: "chart-bg" }));
  if (bandRows) {
    const bx = (b) => xt(new Date(b[0]).getTime()).toFixed(1);
    const area = (a, z) => bandRows.map((b, i) => `${i ? "L" : "M"}${bx(b)},${y(b[a]).toFixed(1)}`).join("") +
      bandRows.slice().reverse().map((b) => `L${bx(b)},${y(b[z]).toFixed(1)}`).join("") + "Z";
    s.appendChild(svg("path", { d: area(1, 5), class: "band outer" }));
    s.appendChild(svg("path", { d: area(2, 4), class: "band inner" }));
    s.appendChild(svg("path", { d: bandRows.map((b, i) => `${i ? "L" : "M"}${bx(b)},${y(b[3]).toFixed(1)}`).join(""), class: "band-mid" }));
  }
  gridlines(s, lo, hi, y, fmt, W, P);
  if (baseline !== null) s.appendChild(svg("line", { x1: P, x2: W - P, y1: y(baseline), y2: y(baseline), class: "chart-base", "stroke-dasharray": "4 4" }));
  if (compare && compare.length > 1) {
    // same x mapping as the main line: by time, or by position when the main line is evenly spaced
    const cx = (k) => (timeX ? xt(new Date(compare[k][0]).getTime()) : P + (k * (W - 2 * P)) / (compare.length - 1));
    const cd = compare.map((c, k) => `${k ? "L" : "M"}${cx(k).toFixed(1)},${y(c[1]).toFixed(1)}`).join("");
    const cp = svg("path", { d: cd, class: "chart-compare", "stroke-width": 1.5 });
    const ct = svg("title", {}); ct.textContent = `${compareLabel}: ${fmt(compare[compare.length - 1][1])} at the end`;
    cp.appendChild(ct);
    s.appendChild(cp);
  }
  if (fill) {
    const gid = "g" + ++gradSeq;
    const grad = svg("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.appendChild(svg("stop", { offset: "0", class: `grad-top ${tone}` }));
    grad.appendChild(svg("stop", { offset: "1", class: `grad-bottom ${tone}` }));
    const defs = svg("defs", {});
    defs.appendChild(grad);
    s.appendChild(defs);
    const top = baseline !== null && baseline >= Math.max(...points.map((p) => p[1])); // drawdown: fill up to zero
    if (top) { grad.setAttribute("y1", 1); grad.setAttribute("y2", 0); }
    const edge = top ? y(baseline) : y(lo);
    const area = svg("path", { d: `${d}L${x(points.length - 1)},${edge}L${x(0)},${edge}Z`, class: "chart-fill grad" });
    area.style.fill = `url(#${gid})`; // inline style beats the stylesheet's flat fill
    s.appendChild(area);
  }
  s.appendChild(svg("path", { d, class: `chart-line ${tone}`, pathLength: 1, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  const end = svg("circle", { cx: x(points.length - 1), cy: y(points[points.length - 1][1]), r: 3.5, class: `chart-end ${tone}` });
  if (timeX) s.appendChild(end);
  const cursor = svg("line", { x1: 0, x2: 0, y1: P, y2: H - P, class: "chart-cursor", "stroke-width": 1, visibility: "hidden" });
  const dot = svg("circle", { r: 4, class: `chart-dot ${tone}`, visibility: "hidden" });
  s.appendChild(cursor); s.appendChild(dot);
  const base = points.length > 2 || !timeX ? `${fmt(Math.min(...points.map((p) => p[1])))} to ${fmt(Math.max(...points.map((p) => p[1])))}` : fmt(points[points.length - 1][1]);
  readout.textContent = base;
  const stamp = (iso) => (timeX ? when(iso) : day(iso));
  s.addEventListener("pointermove", (e) => {
    const rect = s.getBoundingClientRect();
    const rel = ((e.clientX - rect.left) / rect.width) * W;
    let i;
    if (timeX) {
      const t = t0 + ((rel - P) / (W - 2 * P)) * (t1 - t0);
      i = 0;
      for (let k = 0; k < ts.length; k++) if (Math.abs(ts[k] - t) < Math.abs(ts[i] - t)) i = k;
    } else i = Math.max(0, Math.min(points.length - 1, Math.round(((rel - P) / (W - 2 * P)) * (points.length - 1))));
    cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i)); cursor.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(points[i][1])); dot.setAttribute("visibility", "visible");
    readout.textContent = `${stamp(points[i][0])}: ${fmt(points[i][1])}`;
  });
  s.addEventListener("pointerleave", () => {
    cursor.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); readout.textContent = base;
  });
  wrap.appendChild(s);
  const dates = h("div", { class: "inline small muted chart-dates" }, h("span", {}, stamp(points[0][0])),
    h("span", { class: "spacer" }), h("span", {}, stamp(points[points.length - 1][0])));
  wrap.appendChild(dates);
  return wrap;
}
