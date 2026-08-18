import time

from features.cache import live_comparison


class FakeConnection:
    def __init__(self, lane):
        self.lane = lane
        self.closed = False

    def cancel(self):
        pass

    def close(self):
        self.closed = True


def test_equal_concurrency_leaves_readyset_free_to_reach_higher_qps(monkeypatch):
    connections = []
    origin_progress = []

    def open_connection(config):
        connection = FakeConnection(config["lane"])
        connections.append(connection)
        return connection, "postgresql"

    def execute(connection, _query, _engine, controller, **kwargs):
        controller.raise_if_cancelled()
        token = kwargs["on_execute"]() if kwargs.get("on_execute") else None
        time.sleep(0.008 if connection.lane == "origin" else 0.001)
        result = {
            "success": True,
            "execution_time_ms": 8.0
            if connection.lane == "origin"
            else 1.0,
        }
        if kwargs.get("on_complete") is not None:
            kwargs["on_complete"](token)
        return result

    monkeypatch.setattr(
        live_comparison, "_open_persistent_connection", open_connection
    )
    monkeypatch.setattr(live_comparison, "_execute_on_connection", execute)
    controller = live_comparison.LiveComparisonController(4)

    result = live_comparison.run_live_comparison(
        query="SELECT 1",
        original_db_config={"lane": "origin"},
        readyset_db_config={"lane": "readyset"},
        duration_seconds=0.25,
        controller=controller,
        on_origin_progress=lambda token, occurred_at, count: origin_progress.append(
            (token, occurred_at, count)
        ),
    )

    assert result["success"] is True
    assert result["readyset"]["throughput_rps"] > (
        result["origin"]["throughput_rps"] * 2
    )
    assert result["readyset"]["completed"] > result["origin"]["completed"]
    assert len(origin_progress) == result["origin"]["completed"]
    assert all(count == 1 for _token, _occurred_at, count in origin_progress)
    assert connections
    assert all(connection.closed for connection in connections)
