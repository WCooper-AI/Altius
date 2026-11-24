"""Node graph (strategy builder): nodegraph.js functions run in Node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "tradealerts" / "static"
NODE = shutil.which("node")


def run(tmp_path, body):
    """Run JS in Node; body ends with `const out = ...;` or expression. Return parsed JSON."""
    mod = tmp_path / "nodegraph.mjs"
    mod.write_text((STATIC / "nodegraph.js").read_text(encoding="utf-8"), encoding="utf-8")
    script = (
        f"import * as g from {json.dumps(mod.as_uri())}; "
        f"{body} "
        f"console.log(JSON.stringify(out));"
    )
    out = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=30,
        check=True,
    )
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(not NODE, reason="Node isn't installed")


@needs_node
def test_roundtrip_all_shapes(tmp_path):
    """Round trip nested strategy: every shape (all, any, none, notall, negated leaf, comparisons, groups, custom) + exit.when."""
    body = """
const data = {
  entry: {
    all: [
      { any: [
        { left: "close", op: ">", right: { indicator: "rsi", period: 14 } },
        { left: "open", op: "<=", right: 100 }
      ] },
      { not: { weekdays: ["mon", "tue"] } },
      { avoid_news: { before: 30, after: 30, currencies: ["USD"], impact: "high" } },
      { custom_leaf: 1 },
      { not: { left: "high", op: "<", right: 50 } }
    ]
  },
  exit: {
    when: {
      not: {
        all: [
          { time_between: ["09:00", "17:00"], tz: "UTC" },
          { left: "close", op: ">", right: 200 }
        ]
      }
    }
  }
};
const copy = JSON.parse(JSON.stringify(data));
const graph = g.buildGraph(data);
g.applyGraph(graph, data);
const out = JSON.stringify(data) === JSON.stringify(copy);
"""
    assert run(tmp_path, body)


@needs_node
def test_shared_indicator_one_value_node(tmp_path):
    """Same indicator used in entry and exit becomes one value node; inline 30 stays on compare."""
    body = """
const data = {
  entry: { left: "close", op: ">", right: { indicator: "rsi", period: 14 } },
  exit: { when: { left: { indicator: "rsi", period: 14 }, op: "<", right: 30 } }
};
const graph = g.buildGraph(data);
const valueNodes = graph.nodes.filter(n => n.type === "value");
const rsiNodes = valueNodes.filter(n => JSON.stringify(n.data) === JSON.stringify({ indicator: "rsi", period: 14 }));
const compares = graph.nodes.filter(n => n.type === "compare");
const rsiWires = graph.wires.filter(w => rsiNodes.some(r => r.id === w.from));
const out = rsiNodes.length === 1 && rsiWires.length === 2 && compares.some(c => c.data.right === 30);
"""
    assert run(tmp_path, body)


@needs_node
def test_canconnect_rejects_invalid(tmp_path):
    """canConnect rejects: value node into entry.when, node into itself, loop-making wire."""
    body = """
const graph = { seq: 2, sig: "", nodes: [], wires: [] };
["market", "entry", "exit", "risk"].forEach(id => graph.nodes.push({ id, type: id, x: 0, y: 0, data: null }));
const val = g.newNode(graph, "value", 0, 0);
const all = g.newNode(graph, "all", 0, 0);
g.connect(graph, all.id, "entry", "when");
const rejects = {
  value_into_entry: !g.canConnect(graph, val.id, "entry", "when"),
  node_into_self: !g.canConnect(graph, val.id, val.id, "in0"),
  loop_via_all: !g.canConnect(graph, "entry", all.id, "in0")
};
const out = Object.values(rejects).every(v => v);
"""
    assert run(tmp_path, body)


@needs_node
def test_connect_replaces_existing(tmp_path):
    """connect into a port that already has a wire replaces it; still one wire in that port."""
    body = """
const graph = { seq: 2, sig: "", nodes: [], wires: [] };
["market", "entry", "exit", "risk"].forEach(id => graph.nodes.push({ id, type: id, x: 0, y: 0, data: null }));
const a = g.newNode(graph, "value", 0, 0);
const b = g.newNode(graph, "value", 0, 0);
const cmp = g.newNode(graph, "compare", 0, 0);
g.connect(graph, a.id, cmp.id, "left");
const after_a = graph.wires.filter(w => w.to === cmp.id && w.port === "left").length;
g.connect(graph, b.id, cmp.id, "left");
const after_b = graph.wires.filter(w => w.to === cmp.id && w.port === "left").length;
const out = after_a === 1 && after_b === 1 && graph.wires.some(w => w.from === b.id && w.to === cmp.id);
"""
    assert run(tmp_path, body)


@needs_node
def test_inputsof_grows_compacts(tmp_path):
    """inputsOf for all/any grows by one per wire + 1 free; disconnecting a wire compacts, preserves order."""
    body = """
const graph = { seq: 2, sig: "", nodes: [], wires: [] };
["market", "entry", "exit", "risk"].forEach(id => graph.nodes.push({ id, type: id, x: 0, y: 0, data: null }));
const a = g.newNode(graph, "any", 0, 0);
const c1 = g.newNode(graph, "compare", 0, 0);
const c2 = g.newNode(graph, "compare", 0, 0);
const c3 = g.newNode(graph, "compare", 0, 0);
g.connect(graph, c1.id, a.id, "in0");
g.connect(graph, c2.id, a.id, "in1");
g.connect(graph, c3.id, a.id, "in2");
const before = g.inputsOf(graph, a).length;
g.disconnect(graph, a.id, "in1");
const after = g.inputsOf(graph, a).length;
const wires = graph.wires.filter(w => w.to === a.id).sort((a, b) => Number(a.port.slice(2)) - Number(b.port.slice(2)));
const ports = wires.map(w => w.port);
const out = before === 4 && after === 3 && JSON.stringify(ports) === JSON.stringify(["in0", "in1"]);
"""
    assert run(tmp_path, body)


@needs_node
def test_removenode_fixed_returns_false(tmp_path):
    """Removing a fixed node returns false and keeps it."""
    body = """
const graph = { seq: 0, sig: "", nodes: [], wires: [] };
["market", "entry", "exit", "risk"].forEach(id => graph.nodes.push({ id, type: id, x: 0, y: 0, data: null }));
const result = g.removeNode(graph, "entry");
const still_there = graph.nodes.some(n => n.id === "entry");
const out = result === false && still_there;
"""
    assert run(tmp_path, body)


@needs_node
def test_unwired_compare_unused(tmp_path):
    """An unwired compare node is not in usedNodes and doesn't appear in data after applyGraph."""
    body = """
const graph = { seq: 2, sig: "", nodes: [], wires: [] };
["market", "entry", "exit", "risk"].forEach(id => graph.nodes.push({ id, type: id, x: 0, y: 0, data: null }));
const cmp = g.newNode(graph, "compare", 0, 0);
const data = { entry: null, exit: {} };
const used = g.usedNodes(graph);
g.applyGraph(graph, data);
const out = !used.has(cmp.id) && JSON.stringify(data.entry) === JSON.stringify({ all: [] });
"""
    assert run(tmp_path, body)


@needs_node
def test_unwire_exit_when_removes_key(tmp_path):
    """Unwiring exit.when then applyGraph removes data.exit.when; unwiring entry gives {all:[]}."""
    body = """
const graph = { seq: 2, sig: "", nodes: [], wires: [] };
["market", "entry", "exit", "risk"].forEach(id => graph.nodes.push({ id, type: id, x: 0, y: 0, data: null }));
const cmp = g.newNode(graph, "compare", 0, 0);
g.connect(graph, cmp.id, "exit", "when");
let data = { entry: null, exit: { when: { left: "a", op: ">", right: "b" } } };
g.applyGraph(graph, data);
const has_when_before_disconnect = "when" in data.exit;
g.disconnect(graph, "exit", "when");
data = { entry: null, exit: { when: { left: "a", op: ">", right: "b" } } };
g.applyGraph(graph, data);
const has_when_after = "when" in data.exit;
const out = has_when_before_disconnect && !has_when_after;
"""
    assert run(tmp_path, body)


@needs_node
def test_compare_disconnected_side_null(tmp_path):
    """A compare whose wired side is disconnected compiles that side as null."""
    body = """
const graph = { seq: 2, sig: "", nodes: [], wires: [] };
["market", "entry", "exit", "risk"].forEach(id => graph.nodes.push({ id, type: id, x: 0, y: 0, data: null }));
const val = g.newNode(graph, "value", 0, 0);
val.data = { indicator: "rsi", period: 14 };
const cmp = g.newNode(graph, "compare", 0, 0);
cmp.data = { left: null, op: ">", right: 100 };
g.connect(graph, val.id, cmp.id, "left");
let compiled = g.compileNode(graph, cmp.id);
const left_before = compiled.left && compiled.left.indicator === "rsi";
g.disconnect(graph, cmp.id, "left");
compiled = g.compileNode(graph, cmp.id);
const left_after = compiled.left === null;
const out = left_before && left_after;
"""
    assert run(tmp_path, body)


@needs_node
def test_loadgraph_validation(tmp_path):
    """loadGraph returns null for bad JSON and for mismatched sig; returns graph when sig matches."""
    body = """
const data = { entry: { left: "a", op: ">", right: "b" }, exit: {} };
const graph1 = g.buildGraph(data);
const sig = g.rulesSignature(data);
const json_str = JSON.stringify(graph1);
const loaded1 = g.loadGraph(json_str, data);
const loaded_bad_json = g.loadGraph("not json", data);
const data2 = { entry: { left: "x", op: "<", right: "y" }, exit: {} };
const loaded_wrong_sig = g.loadGraph(json_str, data2);
const out = loaded1 !== null && loaded_bad_json === null && loaded_wrong_sig === null;
"""
    assert run(tmp_path, body)


@needs_node
def test_layout_fixed_rightmost(tmp_path):
    """layout: fixed nodes share the right-most x; value x < compare x; min 28px gap between sorted y."""
    body = """
const data = {
  entry: { left: "close", op: ">", right: { indicator: "rsi", period: 14 } }
};
const graph = g.buildGraph(data);
const fixed = graph.nodes.filter(n => ["market", "entry", "exit", "risk"].includes(n.id));
const values = graph.nodes.filter(n => n.type === "value");
const compares = graph.nodes.filter(n => n.type === "compare");
const max_x = Math.max(...graph.nodes.map(n => n.x));
const all_fixed_at_max = fixed.every(n => n.x === max_x);
const values_before_compares = values.every(v => v.x < Math.min(...compares.map(c => c.x)));
const nodes_in_col = new Map();
for (const n of graph.nodes) {
  if (!nodes_in_col.has(n.x)) nodes_in_col.set(n.x, []);
  nodes_in_col.get(n.x).push(n);
}
let gap_ok = true;
for (const col of nodes_in_col.values()) {
  const sorted = col.sort((a, b) => a.y - b.y);
  for (let i = 1; i < sorted.length; i++) {
    const gap = sorted[i].y - (sorted[i - 1].y + 100);
    if (gap < 28) gap_ok = false;
  }
}
const out = all_fixed_at_max && values_before_compares && gap_ok;
"""
    assert run(tmp_path, body)
