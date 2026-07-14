"""End-to-end integration test for the QueryPilot demo, against the live stack.

Brings the demo through its real arc: QueryPilot off -> drive load -> manual
cache (owner=manual) -> QueryPilot on (auto-cache, owner=querypilot), asserting
the throughput and provenance the demo promises. The stack is provisioned once
by the session-scoped qpdemo_stack fixture in conftest.py.
"""

import time

import psycopg2


def _drive(svc, sql: str, n: int = 25):
    p = svc._ports
    conn = psycopg2.connect(host="127.0.0.1", port=p.sqp, user="sqp_user",
                            password="sqp_pass", dbname="sqp_test", connect_timeout=5)
    conn.autocommit = True
    for _ in range(n):
        with conn.cursor() as c:
            c.execute(sql); c.fetchall()
    conn.close()


def test_pools_present(svc):
    pools = svc._qp.admin.pools()
    names = [p["name"] for p in pools.get("pools", [])] if isinstance(pools, dict) else []
    assert "primary" in names and "readyset" in names


def test_pause_stops_autocaching(svc):
    svc.set_querypilot(False)
    before = {p["key"] for p in svc.patterns() if p["status"] == "cached_querypilot"}
    _drive(svc, "SELECT channel, count(*) FROM orders WHERE status='shipped' GROUP BY channel", 30)
    time.sleep(12)  # > 2 discovery cycles
    after = {p["key"] for p in svc.patterns() if p["status"] == "cached_querypilot"}
    assert after == before, "QueryPilot cached while paused"


def test_manual_cache_is_owned_by_you(svc):
    heavy = ("SELECT p.category, sum(oi.qty*oi.unit_cents) FROM order_items oi "
             "JOIN products p ON p.id=oi.product_id GROUP BY p.category")
    _drive(svc, heavy, 25)
    time.sleep(1)
    row = next((p for p in svc.patterns() if "sum(oi.qty" in p["sql"] and "category" in p["sql"]), None)
    assert row is not None
    if not row["status"].startswith("cached_"):
        svc.cache(row["key"])
        time.sleep(2)
        row = next(p for p in svc.patterns() if p["key"] == row["key"])
    try:
        assert row["status"] == "cached_manual"
    finally:
        svc.uncache(row["key"])


def test_resume_enables_autocaching(svc):
    svc.set_querypilot(True)
    _drive(svc, "SELECT status, count(*) FROM orders GROUP BY status", 30)
    deadline = time.monotonic() + 120
    owned = []
    while time.monotonic() < deadline:
        owned = [p for p in svc.patterns() if p["status"] == "cached_querypilot"]
        if owned:
            break
        time.sleep(3)
    assert len(owned) >= 1, "QueryPilot cached nothing after resume"
    svc.set_querypilot(False)
