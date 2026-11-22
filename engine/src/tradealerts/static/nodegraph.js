/* Trading Alerts web interface: The node view's model: a strategy's rules as nodes and wires, and back again.
   Nothing here touches the page (nodes.js draws the graph), so the tests run these functions in Node.

   A graph is { seq, sig, nodes: [{ id, type, x, y, data }], wires: [{ from, to, port }] }.
   A wire runs from one node's output into a named input ("port") of another. The strategy itself stays the
   source of truth: applyGraph writes the rules the wires describe into it, buildGraph reads them back out. */

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const clone = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));

// --------------------------------------------------------------------- the rules tree (editor.js uses these too)

export function groupKind(c) {
  if (!isObj(c)) return null;
  if (Array.isArray(c.all)) return "all";
  if (Array.isArray(c.any)) return "any";
  if (isObj(c.not) && Array.isArray(c.not.any)) return "none";
  if (isObj(c.not) && Array.isArray(c.not.all)) return "notall";
  return null;
}
/** A single condition wrapped in {not: ...} (rules.py supports it; shown as a row with a "not" switch). */
export function isNegatedLeaf(c) {
  return isObj(c) && !groupKind(c) && isObj(c.not) && !groupKind(c.not) && Object.keys(c).length === 1;
}
export function groupItems(c) {
  const k = groupKind(c);
  if (k === "all") return c.all;
  if (k === "any") return c.any;
  if (k === "none") return c.not.any;
  if (k === "notall") return c.not.all;
  return [];
}
/** Always store a strategy's rules as a group, as the list view does. */
function asGroup(c) {
  if (groupKind(c)) return c;
  if (isObj(c) && Object.keys(c).length) return { all: [c] };
  return { all: [] };
}

// --------------------------------------------------------------------- node types

/** What each node is: its title, what its output carries ("cond" = true or false on each candle, "value" = a
    number on each candle) and the add menu group it's in. Fixed nodes are always there and can't be removed;
    "custom" holds a rule the builder has no inputs for, kept exactly as written. */
export const NODE_TYPES = {
  market: { label: "Market", output: null, fixed: true },
  entry: { label: "Entry", output: null, fixed: true },
  exit: { label: "Exit", output: null, fixed: true },
  risk: { label: "Risk and costs", output: null, fixed: true },
  value: { label: "Price or indicator", output: "value", menu: "Values" },
  compare: { label: "Compare", output: "cond", menu: "Conditions" },
  time: { label: "Time window", output: "cond", menu: "Conditions" },
  weekdays: { label: "Weekdays", output: "cond", menu: "Conditions" },
  news: { label: "News filter", output: "cond", menu: "Conditions" },
  all: { label: "All of", output: "cond", menu: "Combine" },
  any: { label: "Any of", output: "cond", menu: "Combine" },
  not: { label: "Not", output: "cond", menu: "Combine" },
  custom: { label: "Custom rule", output: "cond" },
};
const FIXED = ["market", "entry", "exit", "risk"];

/** Starting data for a node added from the menu (the same blanks the list view adds). */
export function blankData(type) {
  switch (type) {
    case "value": return null;
    case "compare": return { left: null, op: "", right: null };
    case "time": return { time_between: ["", ""], tz: "" };
    case "weekdays": return { weekdays: [] };
    case "news": return { avoid_news: { before: null, after: null, currencies: [], impact: "high" } };
    default: return {};
  }
}

/** The inputs a node has right now. All of / Any of grow: one per wire in, plus a free one at the end. */
export function inputsOf(graph, node) {
  switch (node.type) {
    case "entry": return [{ key: "when", kind: "cond", label: "Open a trade when" }];
    case "exit": return [{ key: "when", kind: "cond", label: "Also close when" }];
    case "compare": return [{ key: "left", kind: "value", label: "Left side" }, { key: "right", kind: "value", label: "Right side" }];
    case "not": return [{ key: "in", kind: "cond", label: "Condition" }];
    case "all": case "any": {
      const n = graph.wires.filter((w) => w.to === node.id).length;
      return Array.from({ length: n + 1 }, (_, i) => ({ key: "in" + i, kind: "cond", label: "Condition " + (i + 1) }));
    }
    default: return [];
  }
}

// --------------------------------------------------------------------- editing the graph

export function nodeById(graph, id) { return graph.nodes.find((n) => n.id === id); }
export function wireInto(graph, id, port) { return graph.wires.find((w) => w.to === id && w.port === port); }

function addNode(graph, type, data, x = 0, y = 0, id = null) {
  const node = { id: id || "n" + ++graph.seq, type, x, y, data };
  graph.nodes.push(node);
  return node;
}
/** A new node of this type with blank inputs, at (x, y). */
export function newNode(graph, type, x = 0, y = 0) { return addNode(graph, type, blankData(type), x, y); }

const portIndex = (p) => Number(p.slice(2));
/** Renumbers an All of / Any of node's inputs so they stay in order with no gaps. */
function compact(graph, id) {
  const node = nodeById(graph, id);
  if (!node || (node.type !== "all" && node.type !== "any")) return;
  graph.wires.filter((w) => w.to === id).sort((a, b) => portIndex(a.port) - portIndex(b.port))
    .forEach((w, i) => { w.port = "in" + i; });
}

/** Takes a node and its wires off the canvas. The four fixed nodes stay. */
export function removeNode(graph, id) {
  const node = nodeById(graph, id);
  if (!node || NODE_TYPES[node.type].fixed) return false;
  const fed = new Set(graph.wires.filter((w) => w.from === id).map((w) => w.to));
  graph.nodes = graph.nodes.filter((n) => n !== node);
  graph.wires = graph.wires.filter((w) => w.from !== id && w.to !== id);
  fed.forEach((t) => compact(graph, t));
  return true;
}

/** Does a's output reach b, following wires downstream? */
function feeds(graph, a, b) {
  const seen = new Set(), stack = [a];
  while (stack.length) {
    const id = stack.pop();
    if (id === b) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    for (const w of graph.wires) if (w.from === id) stack.push(w.to);
  }
  return false;
}

/** True if a wire from fromId's output may go into toId's port: the port exists, the kinds match and it
    wouldn't make a loop. */
export function canConnect(graph, fromId, toId, port) {
  const from = nodeById(graph, fromId), to = nodeById(graph, toId);
  if (!from || !to || fromId === toId) return false;
  const input = inputsOf(graph, to).find((p) => p.key === port);
  if (!input || NODE_TYPES[from.type].output !== input.kind) return false;
  return !feeds(graph, toId, fromId);
}
/** Wires fromId into toId's port, replacing whatever was in that port. False if it isn't allowed. */
export function connect(graph, fromId, toId, port) {
  if (!canConnect(graph, fromId, toId, port)) return false;
  graph.wires = graph.wires.filter((w) => !(w.to === toId && w.port === port));
  graph.wires.push({ from: fromId, to: toId, port });
  return true;
}
/** Removes the wire into toId's port, if there is one. */
export function disconnect(graph, toId, port) {
  const before = graph.wires.length;
  graph.wires = graph.wires.filter((w) => !(w.to === toId && w.port === port));
  compact(graph, toId);
  return graph.wires.length !== before;
}

/** Ids of the nodes that feed Entry or Exit, so are part of the strategy. The rest are drawn as not in use. */
export function usedNodes(graph) {
  const used = new Set(FIXED), stack = ["entry", "exit"];
  while (stack.length) {
    const id = stack.pop();
    for (const w of graph.wires) if (w.to === id && !used.has(w.from)) { used.add(w.from); stack.push(w.from); }
  }
  return used;
}

// --------------------------------------------------------------------- graph -> strategy

/** A number typed straight into an input (or left blank) rather than wired in. */
const isInline = (v) => v === null || v === "" || typeof v === "number";

/** The condition or value a node stands for, built from what's wired into it; null if there's nothing usable yet.
    ``path`` holds the nodes above this one, so a loop (which connect() never makes) can't recurse forever. */
export function compileNode(graph, id, path = new Set()) {
  const node = nodeById(graph, id);
  if (!node || path.has(id)) return null;
  const below = new Set(path).add(id);
  const from = (port) => {
    const w = wireInto(graph, id, port);
    return w ? compileNode(graph, w.from, below) : undefined;
  };
  switch (node.type) {
    case "value": return clone(node.data);
    case "compare": {
      const out = clone(node.data);
      for (const side of ["left", "right"]) {
        const v = from(side);
        if (v !== undefined) out[side] = v;
        else if (!isInline(out[side])) out[side] = null;
      }
      return out;
    }
    case "not": {
      const v = from("in");
      return v ? { not: v } : null;
    }
    case "all": case "any":
      return { [node.type]: inputsOf(graph, node).map((p) => from(p.key)).filter((v) => v) };
    case "time": case "weekdays": case "news": case "custom": return clone(node.data);
    default: return null;
  }
}

function fromPort(graph, id, port) {
  const w = wireInto(graph, id, port);
  return w ? compileNode(graph, w.from) : undefined;
}

/** What a graph was built from or last wrote. When a strategy's rules stop matching it (they were changed in
    the list view or the YAML), the node view builds a fresh graph. */
export function rulesSignature(data) {
  return JSON.stringify({ entry: data.entry ?? null, when: (data.exit && data.exit.when) ?? null });
}

/** Writes the rules the wires describe into the strategy: data.entry from what feeds Entry, data.exit.when from
    what feeds Exit (no wire there means no early-close rule). Market, stop, target and risk are edited on their
    nodes directly, so there's nothing to copy for them. */
export function applyGraph(graph, data) {
  data.entry = asGroup(fromPort(graph, "entry", "when"));
  if (!isObj(data.exit)) data.exit = {};
  const when = fromPort(graph, "exit", "when");
  if (when === undefined) delete data.exit.when;
  else data.exit.when = asGroup(when);
  graph.sig = rulesSignature(data);
}

// --------------------------------------------------------------------- strategy -> graph

function leafType(c) {
  if (!isObj(c)) return "custom";
  if ("left" in c) return "compare";
  if (c.time_between) return "time";
  if (c.weekdays) return "weekdays";
  if (c.avoid_news) return "news";
  return "custom";
}

/** The graph for a strategy: the four fixed nodes, plus a node for each condition, group and price or indicator
    (the same price or indicator used twice becomes one node with two wires out). Typed-in numbers stay on their
    Compare node. Laid out left to right, ending at Entry and Exit. */
export function buildGraph(data) {
  const graph = { seq: 0, sig: "", nodes: [], wires: [] };
  for (const t of FIXED) addNode(graph, t, null, 0, 0, t);
  const values = new Map();
  const wire = (from, to, port) => { graph.wires.push({ from, to, port }); };
  const valueNode = (v) => {
    const key = JSON.stringify(v);
    if (!values.has(key)) values.set(key, addNode(graph, "value", clone(v)).id);
    return values.get(key);
  };
  const add = (c) => {
    const kind = groupKind(c);
    if (kind) {
      const g = addNode(graph, kind === "all" || kind === "notall" ? "all" : "any", {});
      groupItems(c).forEach((item, i) => wire(add(item), g.id, "in" + i));
      if (kind === "all" || kind === "any") return g.id;
      const n = addNode(graph, "not", {});
      wire(g.id, n.id, "in");
      return n.id;
    }
    if (isNegatedLeaf(c)) {
      const n = addNode(graph, "not", {});
      wire(add(c.not), n.id, "in");
      return n.id;
    }
    const type = leafType(c);
    if (type !== "compare") return addNode(graph, type, clone(c)).id;
    const node = addNode(graph, "compare", clone(c));
    for (const side of ["left", "right"]) {
      if (isInline(c[side])) continue;
      node.data[side] = null;
      wire(valueNode(c[side]), node.id, side);
    }
    return node.id;
  };
  if (isObj(data.entry) && Object.keys(data.entry).length) wire(add(data.entry), "entry", "when");
  if (isObj(data.exit) && isObj(data.exit.when)) wire(add(data.exit.when), "exit", "when");
  graph.sig = rulesSignature(data);
  return layout(graph);
}

// --------------------------------------------------------------------- saving and restoring

/** A graph saved with JSON.stringify, if it's still for these rules; otherwise null (build a fresh one). */
export function loadGraph(text, data) {
  let g;
  try { g = JSON.parse(text); } catch (_) { return null; }
  if (!isObj(g) || !Array.isArray(g.nodes) || !Array.isArray(g.wires) || g.sig !== rulesSignature(data)) return null;
  if (!FIXED.every((t) => g.nodes.some((n) => n.id === t && n.type === t))) return null;
  if (!g.nodes.every((n) => isObj(n) && NODE_TYPES[n.type] && Number.isFinite(n.x) && Number.isFinite(n.y))) return null;
  const ids = new Set(g.nodes.map((n) => n.id));
  if (!g.wires.every((w) => isObj(w) && ids.has(w.from) && ids.has(w.to) && typeof w.port === "string")) return null;
  g.seq = Math.max(Number(g.seq) || 0, ...g.nodes.map((n) => Number(String(n.id).slice(1)) || 0));
  return g;
}

// --------------------------------------------------------------------- layout

const COL_W = 340, GAP = 28;
/** Rough drawn sizes [width, height], used until nodes.js has measured the real ones. */
const SIZE = { market: [260, 150], entry: [260, 120], exit: [320, 280], risk: [260, 150], value: [250, 120],
  compare: [270, 180], time: [290, 140], weekdays: [290, 120], news: [310, 200], all: [190, 100], any: [190, 100],
  not: [170, 80], custom: [270, 120] };

/** Places every node: one column per step back from Entry and Exit (the fixed nodes on the right, prices and
    indicators furthest left), rows in the order the rules read, pushed apart so nothing overlaps. Nodes that
    don't feed Entry or Exit go in a row underneath. ``sizeOf(id)`` gives a drawn node's [width, height]. */
export function layout(graph, sizeOf = null) {
  const size = (n) => (sizeOf && sizeOf(n.id)) || SIZE[n.type] || [260, 120];
  const depth = new Map();
  const visit = (id, d, path) => {
    if (path.has(id) || (depth.get(id) ?? -1) >= d) return;
    depth.set(id, d);
    const p = new Set(path).add(id);
    for (const w of graph.wires) if (w.to === id) visit(w.from, d + 1, p);
  };
  for (const t of FIXED) visit(t, 0, new Set());

  // Where each node would like its middle: inputs first, a node level with the middle of the inputs placed
  // for it (one fed only by nodes placed earlier, such as a shared indicator, gets a row of its own).
  const centre = new Map(), placed = new Map(), seen = new Set();
  let next = 0;
  const walk = (id) => {
    if (seen.has(id)) return;
    seen.add(id);
    const node = nodeById(graph, id);
    const ins = inputsOf(graph, node).map((p) => wireInto(graph, id, p.key)).filter(Boolean);
    const start = placed.size;
    ins.forEach((w) => walk(w.from));
    placed.set(id, placed.size);
    const ys = ins.filter((w) => placed.get(w.from) >= start).map((w) => centre.get(w.from));
    if (ys.length) centre.set(id, ys.reduce((a, b) => a + b, 0) / ys.length);
    else { const h = size(node)[1]; centre.set(id, next + h / 2); next += h + GAP; }
  };
  walk("entry"); walk("exit");

  const cols = new Map();
  for (const n of graph.nodes) {
    if (!depth.has(n.id)) continue;
    if (!cols.has(depth.get(n.id))) cols.set(depth.get(n.id), []);
    cols.get(depth.get(n.id)).push(n);
  }
  const last = Math.max(0, ...cols.keys());
  let bottom = 0;
  for (const [d, list] of cols) {
    const top = (n) => (centre.has(n.id) ? centre.get(n.id) - size(n)[1] / 2 : 0);
    if (d === 0) list.sort((a, b) => FIXED.indexOf(a.type) - FIXED.indexOf(b.type));
    else list.sort((a, b) => top(a) - top(b));
    let y = -Infinity;
    for (const n of list) {
      n.x = (last - d) * COL_W;
      n.y = Math.max(top(n), y);
      y = n.y + size(n)[1] + GAP;
    }
    bottom = Math.max(bottom, y);
  }
  let x = 0;
  for (const n of graph.nodes) {
    if (depth.has(n.id)) continue;
    n.x = x; n.y = bottom + GAP;
    x += size(n)[0] + GAP;
  }
  return graph;
}
