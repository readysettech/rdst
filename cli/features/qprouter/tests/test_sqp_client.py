"""Unit tests for qprouter fingerprint normalization and rule/reason parsing.

Pure logic — no live stack required.
"""

from features.qprouter.sqp_client import to_decimal, to_hex
from features.qprouter import deploy as qdeploy
from features.qprouter.qprouter import (
    MANUAL_OWNER,
    QPRouter,
    _cache_name,
    _manual_comment,
    _owner,
)


def test_hex_decimal_roundtrip():
    dec = 3833792726822176048
    hx = "0x35345E2C38B64530"
    assert to_decimal(hx) == dec
    assert to_decimal(dec) == dec
    assert to_decimal(str(dec)) == dec
    assert to_hex(dec) == hx
    assert to_hex(hx) == hx
    assert to_decimal(to_hex(dec)) == dec


def test_hex_is_zero_padded_16():
    assert to_hex(1) == "0x0000000000000001"
    assert len(to_hex(1)) == 18  # 0x + 16 digits


def test_cache_name_from_rulemeta():
    rule = {"comment": 'readyset-meta: {"rule_type":"readyset","cache_type":"shallow",'
                        '"cache_name":"readyset_sqp_test_d_0x35345E2C38B64530","version":3}'}
    assert _cache_name(rule) == "readyset_sqp_test_d_0x35345E2C38B64530"


def test_cache_name_absent_or_manual():
    assert _cache_name(None) is None
    assert _cache_name({"comment": _manual_comment(1)}) is None
    assert _cache_name({}) is None


def test_manual_owner_marker():
    comment = _manual_comment(1)
    assert f'"owner":"{MANUAL_OWNER}"' in comment
    assert _owner({"target_pool": "readyset", "comment": comment}) == "manual"
    assert _owner({"target_pool": "readyset", "comment": "readyset-meta: {}"}) == "querypilot"


class FakeAdmin:
    def digests(self, limit=100, order_by="sum_time", min_count=None):
        return [
            {
                "fingerprint_hash": "1",
                "digest_text": "SELECT 1",
                "count_star": 10,
                "sum_time_us": 1000,
            },
            {
                "fingerprint_hash": "2",
                "digest_text": "SELECT 2",
                "count_star": 4,
                "sum_time_us": 900,
            },
            {
                "fingerprint_hash": "3",
                "digest_text": "UPDATE t SET x = 1",
                "count_star": 20,
                "sum_time_us": 800,
            },
            {
                "fingerprint_hash": "4",
                "digest_text": "SELECT 4",
                "count_star": 9,
                "sum_time_us": 700,
            },
        ]

    def pattern_rules(self):
        return [{
            "fingerprint_hash": "1",
            "target_pool": "readyset",
            "enabled": True,
            "comment": 'readyset-meta: {"cache_name":"readyset_sqp_test_d_0x0000000000000001"}',
        }]


class FakeReadySet:
    database = "sqp_test"

    def show_caches(self):
        return []


def test_patterns_compute_querypilot_reasons():
    qp = QPRouter("127.0.0.1", 6432, 9091, api_key=None, readyset=FakeReadySet())
    qp.admin = FakeAdmin()
    rows = {r.fingerprint: r.to_dict() for r in qp.get_patterns(
        query_discovery_mode="sum_time",
        number_of_queries=1,
        min_execution=5,
    )}
    assert rows[1]["decision"]["reason"] == "selected"
    assert rows[2]["decision"] == {"reason": "below_min_execution", "count": 4, "threshold": 5}
    assert rows[3]["decision"]["reason"] == "not_select_shaped"
    assert rows[4]["decision"]["reason"] == "below_rank"
    assert rows[4]["decision"]["winner_values"] == [1000]


def test_reset_querypilot_caches_include_manual_flag():
    # Default preserves hand-created (manual) caches; include_manual clears them
    # too, which is how QueryPilot takes over all caching when it turns on.
    dropped = []

    class Admin:
        def pattern_rules(self):
            return [
                {"fingerprint_hash": "1", "target_pool": "readyset",
                 "comment": 'readyset-meta: {"cache_name":"qp_cache_1"}'},
                {"fingerprint_hash": "2", "target_pool": "readyset",
                 "comment": _manual_comment(2)},
            ]

        def digests(self, limit=100, order_by="sum_time", min_count=None):
            return []

        def drop_pattern_rule(self, fp):
            dropped.append(fp)

    class RS:
        database = "sqp_test"

        def show_caches(self):
            return []

        def drop_cache(self, name):
            pass

    def _fresh_qp():
        qp = QPRouter("127.0.0.1", 6432, 9091, api_key=None, readyset=RS())
        qp.admin = Admin()
        return qp

    _fresh_qp().reset_querypilot_caches()
    assert dropped == [1]  # manual (fp 2) preserved

    dropped.clear()
    _fresh_qp().reset_querypilot_caches(include_manual=True)
    assert sorted(dropped) == [1, 2]  # manual dropped too


def test_qp_crontab_uses_supercronic_seconds_field(tmp_path):
    ports = qdeploy.Ports(pg=5432, readyset=5433, readyset_metrics=6034, sqp=6432, metrics=9090)
    _, _, crontab = qdeploy.write_qp_config(
        tmp_path,
        ports.admin,
        ports.readyset,
        {"user": "u", "password": "p", "database": "d"},
    )

    fields = crontab.read_text().split()
    assert fields[:7] == ["*/15", "*", "*", "*", "*", "*", "*"]


def test_qp_config_defaults_to_budget_ten(tmp_path):
    ports = qdeploy.Ports(pg=5432, readyset=5433, readyset_metrics=6034, sqp=6432, metrics=9090)
    cfg, _, _ = qdeploy.write_qp_config(
        tmp_path,
        ports.admin,
        ports.readyset,
        {"user": "u", "password": "p", "database": "d"},
    )

    assert "number_of_queries = 10" in cfg.read_text()


def test_port_free_detects_a_live_bind():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy = sock.getsockname()[1]
        # A socket actively listening on the host must read as occupied — the
        # same live-bind probe that catches a cache-deploy ReadySet on 5433.
        assert qdeploy._port_free(busy) is False


def test_allocate_ports_skips_an_occupied_base_port(monkeypatch):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy = sock.getsockname()[1]
        # Pin the pg base port onto the occupied socket; allocate_ports must
        # step past it rather than hand back a colliding port.
        monkeypatch.setitem(qdeploy.BASE_PORTS, "pg", busy)
        ports = qdeploy.allocate_ports()
        assert ports.pg != busy
        assert qdeploy._port_free(ports.pg)
