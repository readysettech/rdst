"""Counter retention stays efficient with a large inactive restart baseline."""
from shared.query_registry.observation_store import ObservationStore


def test_prune_skips_inactive_baselines_once_and_preserves_latest_per_epoch(tmp_path):
    with ObservationStore(tmp_path / "cache.db") as store:
        store.record_counter_snapshots(
            "target", "epoch", [{"engine_key": f"idle-{n}", "calls": 1}
                                for n in range(5000)], captured_at=1)
        store.record_counter_snapshots(
            "target", "old-epoch", [{"engine_key": "active-0", "calls": 9}], captured_at=0)
        for captured_at in range(2, 102):
            store.record_counter_snapshots(
                "target", "epoch", [{"engine_key": f"active-{n}", "calls": captured_at}
                                    for n in range(5)], captured_at=captured_at)
        steps = 0

        def progress():
            nonlocal steps
            steps += 1000
            return 0

        store._write_conn.set_progress_handler(progress, 1000)
        try:
            assert store.prune(200, chunk=5) == 495
        finally:
            store._write_conn.set_progress_handler(None, 0)
        # A VM-work budget avoids wall-clock sensitivity while detecting repeated full scans.
        assert steps < 1_000_000
        latest = store.latest_counter_snapshots("target", "epoch")
        assert len(latest) == 5005
        assert {row["captured_at"] for row in latest
                if row["engine_key"].startswith("active-")} == {101}
        assert store.latest_counter_snapshots("target", "old-epoch")[0]["calls"] == 9
