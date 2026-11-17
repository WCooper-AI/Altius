"""Where a strategy is on the way from an idea to live monitoring.

Five stages: Research (the rules are written and valid) → Backtest (tried on past prices) →
Validate (robustness checks on those prices) → Paper trade (a forward test on live prices) →
Monitor (alerts on). ``status`` reports facts only: what has been done, when, and the one
next step. It never judges the results themselves.
"""

from __future__ import annotations

from typing import Any

ORDER = ("research", "backtest", "validate", "paper", "monitor")
LABELS = {"research": "Research", "backtest": "Backtest", "validate": "Validate", "paper": "Paper trade",
          "monitor": "Monitor"}
# Test kinds recorded by web/routes/testing.py; the ones that count as a robustness check.
VALIDATION = ("luck", "split", "optimise")
TEST_NAMES = {"backtest": "Backtest", "split": "Train/test split", "luck": "Luck check", "optimise": "Optimiser",
              "sweep": "Settings comparison"}

NEXT = {
    "research": "Fix the problem with the strategy so it can be tested.",
    "backtest": "Run a backtest to see how the rules did on past prices.",
    "validate": "Run a luck check or a train/test split to see how much of the result could be chance.",
    "paper": "Start a forward test to follow the rules on live prices without risking money.",
    "monitor": "Turn on alerts to be told when the rules fire.",
    "monitor_no_channel": "Set up an alert channel in Settings, then turn on alerts.",
    "done": "Alerts are on. As live trades come in, compare them with the backtest's usual range on the "
            "forward test's page.",
}


def alerts_on(data: dict | None) -> bool:
    """The same rule as ``statusOf`` in describe.js: saved as live and not paused."""
    d = data or {}
    return d.get("template") is False and d.get("enabled", True) is not False


def _latest(tests: list[dict], kinds: tuple[str, ...]) -> dict | None:
    hits = [t for t in tests if t.get("kind") in kinds and t.get("at")]
    return max(hits, key=lambda t: t["at"]) if hits else None


def status(data: dict | None, tests: list[dict], run: dict | None = None, *, error: str | None = None,
           rules_hash: str | None = None, channels: bool = True, earlier_runs: int = 0) -> dict[str, Any]:
    """The strategy's stage block: ``steps`` (one per stage with ``state`` done / todo, a short
    ``detail``, and ``at`` for the stages that have a time), ``current`` (the first stage not
    done, or ``monitor`` when every stage is done) and ``next`` (the one thing to do now).

    ``tests`` are the store's test records; ``rules_hash`` is the saved rules' fingerprint, so a
    test run on different rules is marked ``changed``. ``run`` is the active forward test.
    ``channels`` says whether any alert channel is set up; ``earlier_runs`` counts stopped
    forward tests."""
    steps = []

    if error:
        steps.append({"key": "research", "state": "todo", "detail": "Can't be loaded: " + error})
    else:
        steps.append({"key": "research", "state": "done", "detail": "Rules saved and valid"})

    bt = _latest(tests, ("backtest", "split"))
    if bt:
        changed = bool(rules_hash) and bool(bt.get("rules_hash")) and bt["rules_hash"] != rules_hash
        steps.append({"key": "backtest", "state": "done", "at": bt["at"], "changed": changed,
                      "detail": "Rules changed since" if changed else "Tested on past prices"})
    else:
        steps.append({"key": "backtest", "state": "todo", "detail": "Not yet tested on past prices"})

    val = _latest(tests, VALIDATION)
    if val:
        changed = bool(rules_hash) and bool(val.get("rules_hash")) and val["rules_hash"] != rules_hash
        name = TEST_NAMES.get(val["kind"], val["kind"])
        steps.append({"key": "validate", "state": "done", "at": val["at"], "changed": changed,
                      "detail": name + (", rules changed since" if changed else "")})
    else:
        steps.append({"key": "validate", "state": "todo", "detail": "No robustness check yet"})

    if run:
        steps.append({"key": "paper", "state": "done", "at": run.get("started_at"), "detail": "Forward test running"})
    elif earlier_runs:
        steps.append({"key": "paper", "state": "done",
                      "detail": f"{earlier_runs} earlier forward test{'s' if earlier_runs != 1 else ''}, none running"})
    else:
        steps.append({"key": "paper", "state": "todo", "detail": "Not forward tested"})

    live = alerts_on(data)
    steps.append({"key": "monitor", "state": "done" if live else "todo", "detail": "Alerts on" if live else "Alerts off"})

    for s in steps:
        s["label"] = LABELS[s["key"]]
    todo = [s["key"] for s in steps if s["state"] != "done"]
    current = todo[0] if todo else "monitor"
    if not todo:
        text = NEXT["done"]
    elif current == "monitor" and not channels:
        text = NEXT["monitor_no_channel"]
    else:
        text = NEXT[current]
    return {"steps": steps, "current": current, "done": len(steps) - len(todo), "complete": not todo,
            "next": {"key": current if todo else None, "text": text}}


def test_kinds(body: dict) -> list[str]:
    """Which test records a backtest request should leave: a plain backtest, plus a train/test
    split when the request asked for one."""
    return ["backtest", "split"] if body.get("split_pct") else ["backtest"]
