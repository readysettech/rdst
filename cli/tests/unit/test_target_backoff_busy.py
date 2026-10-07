from shared.target_backoff import TargetBackoff


def test_busy_only_while_another_reader_holds_a_healthy_database():
    now = [0.0]
    backoff = TargetBackoff(clock=lambda: now[0])
    assert not backoff.busy("db")
    assert backoff.acquire("db")
    assert backoff.busy("db")
    backoff.finish("db", success=False)
    assert not backoff.busy("db")
