"""Jobs list stays newest first when two jobs share a clock tick (coarse clock on Windows)."""
from tradealerts import jobs


def test_same_clock_tick_keeps_newest_first(monkeypatch):
    # Patch time.time to return the same value for both jobs
    monkeypatch.setattr(jobs.time, "time", lambda: 1_700_000_000.0)

    runner = jobs.InlineJobs()
    first = runner.start("me", lambda progress: None)
    second = runner.start("me", lambda progress: None)

    # Undo the patch
    monkeypatch.undo()

    # Newest first by order
    assert [j["id"] for j in runner.list("me")] == [second, first]
