/* Trading Alerts web interface: a plain-JS command menu that works like cmdk (https://github.com/dip/cmdk).
   Same parts and attributes as cmdk ([cmdk-root], [cmdk-input], [cmdk-list], [cmdk-group], [cmdk-item], [cmdk-empty],
   [cmdk-loading], data-selected, --cmdk-list-height), the same keys and the same fuzzy ranking (commandScore below).
   No imports, so the scoring can also be run on its own. */

// ----- commandScore: ported from cmdk's command-score.ts (MIT, Copyright (c) 2018 Superhuman, via cmdk)
const SCORE_CONTINUE_MATCH = 1, SCORE_SPACE_WORD_JUMP = 0.9, SCORE_NON_SPACE_WORD_JUMP = 0.8, SCORE_CHARACTER_JUMP = 0.17,
  SCORE_TRANSPOSITION = 0.1, PENALTY_SKIPPED = 0.999, PENALTY_CASE_MISMATCH = 0.9999, PENALTY_NOT_COMPLETE = 0.99;
const IS_GAP_REGEXP = /[\\/_+.#"@[({&]/, COUNT_GAPS_REGEXP = /[\\/_+.#"@[({&]/g, IS_SPACE_REGEXP = /[\s-]/, COUNT_SPACE_REGEXP = /[\s-]/g;

function scoreInner(str, abbr, lowStr, lowAbbr, si, ai, memo) {
  if (ai === abbr.length) return si === str.length ? SCORE_CONTINUE_MATCH : PENALTY_NOT_COMPLETE;
  const key = si + "," + ai;
  if (memo[key] !== undefined) return memo[key];
  const ch = lowAbbr.charAt(ai);
  let index = lowStr.indexOf(ch, si), high = 0;
  while (index >= 0) {
    let score = scoreInner(str, abbr, lowStr, lowAbbr, index + 1, ai + 1, memo);
    if (score > high) {
      if (index === si) score *= SCORE_CONTINUE_MATCH;
      else if (IS_GAP_REGEXP.test(str.charAt(index - 1))) {
        score *= SCORE_NON_SPACE_WORD_JUMP;
        const breaks = str.slice(si, index - 1).match(COUNT_GAPS_REGEXP);
        if (breaks && si > 0) score *= Math.pow(PENALTY_SKIPPED, breaks.length);
      } else if (IS_SPACE_REGEXP.test(str.charAt(index - 1))) {
        score *= SCORE_SPACE_WORD_JUMP;
        const breaks = str.slice(si, index - 1).match(COUNT_SPACE_REGEXP);
        if (breaks && si > 0) score *= Math.pow(PENALTY_SKIPPED, breaks.length);
      } else {
        score *= SCORE_CHARACTER_JUMP;
        if (si > 0) score *= Math.pow(PENALTY_SKIPPED, index - si);
      }
      if (str.charAt(index) !== abbr.charAt(ai)) score *= PENALTY_CASE_MISMATCH;
    }
    if ((score < SCORE_TRANSPOSITION && lowStr.charAt(index - 1) === lowAbbr.charAt(ai + 1)) ||
        (lowAbbr.charAt(ai + 1) === lowAbbr.charAt(ai) && lowStr.charAt(index - 1) !== lowAbbr.charAt(ai))) {
      const transposed = scoreInner(str, abbr, lowStr, lowAbbr, index + 1, ai + 2, memo);
      if (transposed * SCORE_TRANSPOSITION > score) score = transposed * SCORE_TRANSPOSITION;
    }
    if (score > high) high = score;
    index = lowStr.indexOf(ch, index + 1);
  }
  memo[key] = high;
  return high;
}
const squash = (s) => s.toLowerCase().replace(COUNT_SPACE_REGEXP, " ");

/** 0 (no match) to 1 (exact). Keywords count as extra words after the value, as in cmdk. */
export function commandScore(value, search, keywords) {
  const str = keywords && keywords.length ? value + " " + keywords.join(" ") : value;
  return scoreInner(str, search, squash(str), squash(search), 0, 0, {});
}

// ----- the menu
let uid = 0;
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k);
  return e;
}

/**
 * Build a command menu.
 *   groups: [{ heading, items: [{ value, keywords?, icon?, hint?, onSelect }] }]
 *   options: { label, placeholder, empty, loop, onDone }  (onDone runs after an item is chosen)
 * Returns { root, input, setGroups(groups), setLoading(on), search(text) }.
 */
export function createCommand(groups, options = {}) {
  const id = "cmdk" + ++uid, listId = id + "-list";
  const input = el("input", { "cmdk-input": true, type: "text", role: "combobox", autocomplete: "off", autocorrect: "off", spellcheck: "false",
    "aria-autocomplete": "list", "aria-expanded": "true", "aria-controls": listId, "aria-label": options.label || "Search",
    placeholder: options.placeholder || "Type a command or search…" });
  const sizer = el("div", { "cmdk-list-sizer": true });
  const list = el("div", { "cmdk-list": true, role: "listbox", id: listId, tabindex: "-1", "aria-label": "Suggestions" }, sizer);
  const empty = el("div", { "cmdk-empty": true, role: "presentation" }, options.empty || "No results found.");
  const loading = el("div", { "cmdk-loading": true, role: "progressbar", "aria-label": "Loading…", hidden: true }, "Loading…");
  const root = el("div", { "cmdk-root": true, tabindex: "-1" }, input, list);
  const loop = options.loop !== false;
  let entries = [], selected = null;   // entries: { group, el, item, score }

  // cmdk animates the list's height through --cmdk-list-height; set via the CSSOM so a strict CSP allows it.
  if (window.ResizeObserver) new ResizeObserver(() => list.style.setProperty("--cmdk-list-height", sizer.offsetHeight.toFixed(1) + "px")).observe(sizer);

  function setGroups(next) {
    sizer.replaceChildren();
    entries = [];
    next.forEach((g, gi) => {
      const headId = `${id}-g${gi}`;
      const items = el("div", { "cmdk-group-items": true, role: "group", "aria-labelledby": g.heading ? headId : null });
      const group = el("div", { "cmdk-group": true, role: "presentation", "data-value": g.heading || "" },
        g.heading ? el("div", { "cmdk-group-heading": true, id: headId, "aria-hidden": "true" }, g.heading) : null, items);
      g.items.forEach((item, ii) => {
        const row = el("div", { "cmdk-item": true, role: "option", id: `${id}-${gi}-${ii}`, "aria-selected": "false", "data-value": item.value },
          item.icon ? el("span", { "cmdk-item-icon": true, "aria-hidden": "true" }, item.icon) : null,
          el("span", { "cmdk-item-label": true }, item.value),
          item.hint ? el("span", { "cmdk-item-hint": true }, item.hint) : null);
        const entry = { group, items, el: row, item, score: 1, order: entries.length };
        row.addEventListener("pointermove", () => { if (selected !== entry) select(entry, false); });
        row.addEventListener("click", () => choose(entry));
        items.append(row);
        entries.push(entry);
      });
      sizer.append(group);
    });
    sizer.append(loading, empty);
    filter();
  }

  // Score every item; hide misses; with a search, sort items by score and groups by their best item (as cmdk does).
  function filter() {
    const q = input.value.trim();
    const groupBest = new Map();
    for (const e of entries) {
      e.score = q ? commandScore(e.item.value, q, e.item.keywords) : 1;
      e.el.hidden = e.score <= 0;
      groupBest.set(e.group, Math.max(groupBest.get(e.group) || 0, e.score));
    }
    for (const [g, best] of groupBest) g.hidden = best <= 0;
    const ranked = q ? [...entries].sort((a, b) => b.score - a.score || a.order - b.order) : entries;
    for (const e of ranked) e.items.append(e.el);
    if (q) [...groupBest.keys()].sort((a, b) => groupBest.get(b) - groupBest.get(a)).forEach((g) => sizer.insertBefore(g, loading));
    else [...groupBest.keys()].forEach((g) => sizer.insertBefore(g, loading));
    const shown = visible();
    empty.hidden = shown.length > 0 || !loading.hidden;
    select(shown[0] || null);
  }

  function visible() {
    return [...sizer.querySelectorAll("[cmdk-item]:not([hidden])")].map((row) => entries.find((e) => e.el === row))
      .filter((e) => e && !e.group.hidden);
  }

  function select(entry, scroll = true) {
    if (selected) { selected.el.removeAttribute("data-selected"); selected.el.setAttribute("aria-selected", "false"); }
    selected = entry;
    if (!entry) { input.removeAttribute("aria-activedescendant"); return; }
    entry.el.setAttribute("data-selected", "true");
    entry.el.setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", entry.el.id);
    if (!scroll) return;
    const first = entry.items.querySelector("[cmdk-item]:not([hidden])") === entry.el;
    const head = entry.group.querySelector("[cmdk-group-heading]");
    (first && head ? head : entry.el).scrollIntoView({ block: "nearest" });
  }

  function move(step) {
    const shown = visible();
    if (!shown.length) return;
    let i = shown.indexOf(selected) + step;
    if (i < 0) i = loop ? shown.length - 1 : 0;
    if (i >= shown.length) i = loop ? 0 : shown.length - 1;
    select(shown[i]);
  }
  function moveGroup(step) {
    const shown = visible();
    const groups = [...new Set(shown.map((e) => e.group))];
    const gi = groups.indexOf(selected ? selected.group : groups[0]) + step;
    const g = groups[Math.max(0, Math.min(groups.length - 1, gi))];
    const target = shown.find((e) => e.group === g);   // the first item of the next or previous group
    if (target) select(target);
  }
  function choose(entry) {
    if (!entry) return;
    if (options.onDone) options.onDone(entry.item);
    entry.item.onSelect(entry.item);
  }

  input.addEventListener("input", filter);
  root.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    const vim = e.ctrlKey && !e.metaKey && !e.altKey;
    const k = e.key;
    if (k === "ArrowDown" || (vim && (k === "n" || k === "j"))) e.altKey ? moveGroup(1) : e.metaKey ? select(visible().at(-1)) : move(1);
    else if (k === "ArrowUp" || (vim && (k === "p" || k === "k"))) e.altKey ? moveGroup(-1) : e.metaKey ? select(visible()[0]) : move(-1);
    else if (k === "Home") select(visible()[0]);
    else if (k === "End") select(visible().at(-1));
    else if (k === "Enter") choose(selected);
    else return;
    e.preventDefault();
  });

  setGroups(groups);
  return {
    root, input, setGroups,
    setLoading(on) { loading.hidden = !on; empty.hidden = !!on || visible().length > 0; },
    search(text) { input.value = text; filter(); },
  };
}

/** cmdk's Command.Dialog: the menu in a modal <dialog>. Escape, a click outside or choosing an item closes it. */
export function commandDialog(groups, options = {}) {
  let dlg = null;
  const cmd = createCommand(groups, { ...options, onDone: (item) => { dlg.close(); if (options.onDone) options.onDone(item); } });
  dlg = el("dialog", { "cmdk-dialog": true, class: options.className || null, "aria-label": options.label || "Command menu" },
    cmd.root, options.footer || null);
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener("close", () => { dlg.remove(); if (options.onClose) options.onClose(); });
  document.body.append(dlg);
  dlg.showModal();
  cmd.input.focus();
  return { dialog: dlg, ...cmd, close: () => dlg.close() };
}
