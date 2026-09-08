import pytest, sqlglot
from sqlglot import exp
from features.ask.endpoint_component import (
    plan_endpoint_component,
    prove_endpoint_component,
    accepts_endpoint_count,
)
from types import SimpleNamespace as S

SCHEMA = S(
    tables={
        "connections": S(
            name="connections",
            columns={
                k: S(name=k, data_type="varchar(50)")
                for k in ["node_id", "node_id2", "edge_id"]
            },
        )
    }
)
SQL = (
    "SELECT COUNT(*) AS total FROM connections WHERE node_id = '12' OR node_id2 = '12'"
)


def rows():
    result = []
    for namespace, n in [("G1", 4), ("G1", 6), ("G2", 8)]:
        e = f"{namespace}_{n}"
        a = f"{namespace}_12"
        b = f"{namespace}_{n}"
        result.extend([[e, a, b], [e, b, a]])
    return result


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_rewrite_and_complete_edge_scope(dialect):
    p = plan_endpoint_component(SQL, dialect, SCHEMA)
    assert p
    d = "postgres" if dialect == "postgresql" else dialect
    a = sqlglot.parse_one(SQL, read=d)
    b = sqlglot.parse_one(p.candidate_sql, read=d)
    b.find(exp.Count).replace(a.find(exp.Count).copy())
    b.set("where", a.args["where"].copy())
    assert a == b
    proof = prove_endpoint_component(p, [[0]], rows())
    assert proof["edge_count"] == 3
    assert accepts_endpoint_count(proof, [[3]]) and not accepts_endpoint_count(
        proof, [[6]]
    )


@pytest.mark.parametrize(
    "query",
    [
        SQL + " LIMIT 1",
        SQL + " GROUP BY node_id",
        SQL.replace("'12'", "'012'"),
        SQL.replace("'12'", "12"),
        SQL.replace("COUNT(*)", "COUNT(DISTINCT edge_id)"),
        SQL.replace("node_id = '12' OR node_id2 = '12'", "node_id = '12'"),
        SQL.replace(" OR ", " AND "),
        SQL.replace("node_id2 = '12'", "node_id2 = '13'"),
        SQL + "; DELETE FROM connections",
        SQL.replace("connections", "other"),
    ],
)
def test_unsupported_query(query):
    assert plan_endpoint_component(query, "mysql", SCHEMA) is None


@pytest.mark.parametrize(
    "bad",
    [
        [],
        rows()[:-1],
        rows() + rows()[:2],
        [[e, b, a] if i == 0 else [e, a, b] for i, (e, a, b) in enumerate(rows())],
        [[e, "G1_12", "G2_4"] for e, a, b in rows()],
        [[e, a, a] for e, a, b in rows()],
        [[e, a.replace("_12", "_112"), b.replace("_12", "_112")] for e, a, b in rows()],
        [[e, a + " ", b] for e, a, b in rows()],
        rows() * 1667,
    ],
)
def test_incomplete_or_ambiguous_edges(bad):
    assert (
        prove_endpoint_component(
            plan_endpoint_component(SQL, "mysql", SCHEMA), [[0]], bad
        )
        is None
    )


@pytest.mark.parametrize("before", [[[1]], [[0], [0]], [[None]], [[False]], [[0.0]]])
def test_original_must_be_integer_zero(before):
    # Bool and float compare equal in Python; the proof needs strict count types.
    assert (
        prove_endpoint_component(
            plan_endpoint_component(SQL, "mysql", SCHEMA), before, rows()
        )
        is None
    )


def test_storage_metadata_never_claims_semantic_scope():
    from features.ask.endpoint_component import verified_storage_facts

    p = plan_endpoint_component(SQL, "mysql", SCHEMA)
    facts = verified_storage_facts(p, [[0]], rows())
    assert facts["endpoint_columns"] == [p.left_column, p.right_column]
    assert facts["edge_identifier_column"] == p.edge_column
    assert facts["complete_incident_edge_rows_verified"] is True
    assert "do not establish user intent" in facts["scope_limitation"]
    assert verified_storage_facts(p, [[0]], rows()[:-1]) is None
    assert verified_storage_facts(p, [[1]], rows()) is None


@pytest.mark.parametrize("kind", ["uuid", "pair-components", "arbitrary"])
def test_opaque_edge_identifiers_are_proven_by_complete_pairs(kind):
    data = rows()
    for i, row in enumerate(data):
        group = i // 2
        row[0] = [
            "f55f8c12-7a2c-47ef-922b-" + str(group),
            "G_" + str(group) + "_12_4",
            "edge key " + str(group),
        ][["uuid", "pair-components", "arbitrary"].index(kind)]
    p = plan_endpoint_component(SQL, "mysql", SCHEMA)
    proof = prove_endpoint_component(p, [[0]], data)
    assert proof["edge_count"] == 3 and proof["namespaces"] == 2
    from features.ask.endpoint_component import verified_storage_facts

    facts = verified_storage_facts(p, [[0]], data)
    assert facts["each_edge_connects_endpoints_in_one_namespace"] is True
    assert "opaque" in facts["edge_identifier_format"]
    assert "each_edge_and_its_endpoints_share_a_namespace" not in facts


def test_reused_opaque_identifier_cannot_merge_distinct_edges():
    data = rows()
    for row in data:
        row[0] = "same-key"
    assert (
        prove_endpoint_component(
            plan_endpoint_component(SQL, "mysql", SCHEMA), [[0]], data
        )
        is None
    )


@pytest.mark.parametrize("opaque", [False, True])
def test_final_semantic_prompt_preserves_complete_schema(opaque):
    import json
    from features.ask.endpoint_component import (
        route_endpoint_component,
        verified_storage_facts,
    )

    data = rows()
    if opaque:
        for row in data:
            row[0] = "opaque-" + row[0]
    p = plan_endpoint_component(SQL, "mysql", SCHEMA)
    facts = verified_storage_facts(p, [[0]], data)

    class Capture:
        def generate_response(self, **kw):
            payload = json.loads(kw["prompt"])
            assert payload["complete_schema"] == {
                name: {key: str(col.data_type) for key, col in table.columns.items()}
                for name, table in SCHEMA.tables.items()
            }
            assert isinstance(payload["trigger_catalog"], str)
            return {"response": "{}"}

    route_endpoint_component(
        "Count local edges.", SQL, "mysql", p, SCHEMA, facts, Capture()
    )
