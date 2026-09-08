from features.ask import endpoint_component as m
from types import SimpleNamespace as S
import pytest, sqlglot
from sqlglot import exp
from features.ask.endpoint_component import plan_endpoint_component as old


def schema(kind):
    return S(
        tables={
            "links": S(
                name="links",
                columns={
                    k: S(name=k, data_type="varchar(60)")
                    for k in [kind + "_id", kind + "_id2", "edge_id"]
                },
            )
        }
    )


def query(kind, literal):
    return f"SELECT COUNT(*) AS n FROM links WHERE {kind}_id = '{literal}' OR {kind}_id2 = '{literal}'"


@pytest.mark.parametrize("kind", ["node", "vertex", "terminal", "junction"])
@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_prefix_is_only_representation_proposal(kind, dialect):
    s = schema(kind)
    sql = query(kind, kind + "12")
    p = m.plan_endpoint_component(sql, dialect, s)
    bare = old(query(kind, "12"), dialect, s)
    assert (
        p
        and p.number == "12"
        and p.kind_prefix == kind
        and p.candidate_sql == bare.candidate_sql
    )
    d = "postgres" if dialect == "postgresql" else dialect
    a = sqlglot.parse_one(sql, read=d)
    b = sqlglot.parse_one(p.candidate_sql, read=d)
    b.find(exp.Count).replace(a.find(exp.Count).copy())
    b.set("where", a.args["where"].copy())
    assert a == b
    proof = sqlglot.parse_one(p.proof_sql, read=d)
    assert (
        sum(isinstance(x, exp.Literal) and x.this == kind + "12" for x in proof.walk())
        == 4
    )


@pytest.mark.parametrize(
    "literal",
    [
        "edge12",
        "unknown12",
        "nodes12",
        "node012",
        "node1234567",
        "node-12",
        "node_12",
        "node 12",
        "node12x",
        "node１２",
        "node+12",
        "node12 OR 1=1",
        "Node12 ",
        "'12",
    ],
)
def test_other_literal_forms_rejected(literal):
    assert (
        m.plan_endpoint_component(query("node", literal), "mysql", schema("node"))
        is None
    )
