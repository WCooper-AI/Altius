import threading
from pathlib import Path

from tradealerts.state import StateStore


def _start_concurrently(db_path: Path, *, if_none: bool, threads: int = 8) -> list[dict]:
    barrier = threading.Barrier(threads)
    results: list[dict] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        store = StateStore(db_path)
        try:
            barrier.wait()
            run = store.start_run(
                "strat-1",
                name="Test Strategy",
                market="EUR/USD",
                timeframe="1h",
                currency="USD",
                start_balance=1000.0,
                if_none=if_none,
            )
            with lock:
                results.append(run)
        except BaseException as exc:  # pragma: no cover - surfaced by assertion
            with lock:
                errors.append(exc)
        finally:
            store.close()

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()

    assert not errors
    return results


def test_start_run_race_leaves_single_active_run(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    StateStore(db_path).close()

    _start_concurrently(db_path, if_none=False)

    store = StateStore(db_path)
    try:
        active = store.db.execute(
            "SELECT * FROM runs WHERE strategy_id = ? AND stopped_at IS NULL",
            ("strat-1",),
        ).fetchall()
        assert len(active) == 1
    finally:
        store.close()


def test_start_run_if_none_reuses_single_run(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    StateStore(db_path).close()

    runs = _start_concurrently(db_path, if_none=True)

    assert len({r["id"] for r in runs}) == 1
    store = StateStore(db_path)
    try:
        all_runs = store.db.execute(
            "SELECT * FROM runs WHERE strategy_id = ?",
            ("strat-1",),
        ).fetchall()
        assert len(all_runs) == 1
    finally:
        store.close()
