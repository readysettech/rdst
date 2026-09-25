from features.monitor.attention import baselines, group_many, shortlist


def row(key, calls, total_ms):
    return {"query_key": key, "calls": calls, "total_ms": total_ms}


def test_baselines_are_per_query_and_sparse_history_is_safe():
    current = [row("a", 30, 300), row("b", 5, 100)]
    past = {"1h": [row("a", 60, 300)], "1d": [], "7d": [row("a", 100, 1000)]}
    result = {item["query_key"]: item for item in baselines(current, past)}
    assert result["a"]["ratios"]["1h"]["mean_ms"] == 2
    assert result["a"]["ratios"]["1d"]["qps"] is None
    assert result["b"]["ratios"]["7d"]["mean_ms"] is None
    assert result["b"]["db_time_share"] == 0.25


def test_shortlist_caps_at_fifteen_and_includes_stuck_new_and_stopped():
    current = [row(f"q{i}", 100, 1000 + i * 100) for i in range(20)]
    current.append(row("stuck", 1, 1))
    history = {"1h": [row("stopped", 3600, 10000)] +
               [row(f"q{i}", 100, 100) for i in range(20)]}
    rows = baselines(current, history)
    chosen = shortlist(rows, {"stuck"})
    assert len(chosen) == 15
    assert any(item["query_key"] == "stuck" for item in chosen)
    assert any(item["query_key"] == "stopped" for item in chosen)
    assert all(item["reason"] in {"stuck", "stopped", "new_heavy", "mover"} for item in chosen)


def test_many_changes_grouping():
    changes = [{"query_key": str(i), "attention": "notify"} for i in range(6)]
    grouped = group_many(changes)
    assert len(grouped) == 1
    assert grouped[0]["subject"] == "database"
    assert len(group_many(changes[:5])) == 5
    assert group_many(changes[:2], "database_wide")[0]["type"] == "many_queries_changed"
