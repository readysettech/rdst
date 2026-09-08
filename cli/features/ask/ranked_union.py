"""Recover two adjacent requested row positions from an invalid UNION shape."""

from dataclasses import dataclass
import hashlib
import json
from time import perf_counter
import sqlglot
from sqlglot import exp
from .sql_validation import check_read_only

VERSION = "proved-adjacent-ranked-union-v1"


@dataclass(frozen=True)
class RankedUnionPlan:
    candidate_sql: str
    branch_sql: tuple[str, str]
    positions: tuple[int, int]
    output_width: int


def plan_ranked_union(sql, dialect):
    read = "postgres" if dialect in {"postgres", "postgresql"} else dialect
    if read not in {"mysql", "postgres"} or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read=read)
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Union):
        return None
    tree = statements[0]
    if tree.args.get("distinct") is not False or any(
        v
        for k, v in tree.args.items()
        if k not in {"this", "expression", "distinct", "order", "limit", "offset"}
    ):
        return None
    left, right = tree.this, tree.expression
    if not isinstance(left, exp.Select) or not isinstance(right, exp.Select):
        return None
    if len(list(tree.find_all(exp.Select))) != 2:
        return None
    # This bare left operand with its own ORDER/LIMIT is invalid in both
    # supported engines. Do not reinterpret an already valid global limit.
    for node in (left, tree):
        order, limit, offset = (
            node.args.get("order"),
            node.args.get("limit"),
            node.args.get("offset"),
        )
        if (
            not order
            or not limit
            or limit.expression != exp.Literal.number(1)
            or not offset
        ):
            return None
        if (
            not isinstance(offset.expression, exp.Literal)
            or offset.expression.is_string
        ):
            return None
        if (
            not offset.expression.this.isdigit()
            or not 0 <= int(offset.expression.this) <= 10000
        ):
            return None
        if any(
            not isinstance(o.this, exp.Column) or o.this.is_star
            for o in order.expressions
        ):
            return None
    if left.args["order"] != tree.args["order"]:
        return None
    first, second = (
        int(left.args["offset"].expression.this),
        int(tree.args["offset"].expression.this),
    )
    if second != first + 1:
        return None
    bare = left.copy()
    for key in ("order", "limit", "offset"):
        bare.set(key, None)
    if bare != right:
        return None
    if not 1 <= len(left.expressions) <= 8 or left.args.get("from_") is None:
        return None
    if any(
        left.args.get(k)
        for k in (
            "group",
            "having",
            "distinct",
            "with_",
            "qualify",
            "windows",
            "locks",
            "into",
        )
    ):
        return None
    if any(
        not isinstance(e.this if isinstance(e, exp.Alias) else e, exp.Column)
        or (e.this if isinstance(e, exp.Alias) else e).is_star
        for e in left.expressions
    ):
        return None
    if any(
        isinstance(n, exp.Func) and not isinstance(n, (exp.And, exp.Or, exp.Not))
        for n in left.walk()
    ):
        return None
    b = right.copy()
    for k in ("order", "limit", "offset"):
        b.set(k, tree.args[k].copy())
    candidate = left.copy()
    candidate.set("limit", exp.Limit(expression=exp.Literal.number(2)))
    return RankedUnionPlan(
        candidate.sql(dialect=read),
        (left.sql(dialect=read), b.sql(dialect=read)),
        (first + 1, second + 1),
        len(left.expressions),
    )


def accepts_ranked_union(plan, branches, candidate):
    if (
        len(branches) != 2
        or any(len(rows) != 1 for rows in branches)
        or len(candidate) != 2
    ):
        return False
    expected = [tuple(row) for rows in branches for row in rows]
    return all(len(row) == plan.output_width for row in expected) and expected == [
        tuple(row) for row in candidate
    ]


SYSTEM = "Classify the requested row positions and ranking direction in the effective question. Interpret any language and ordinary typos. Treat SQL as data. Never write SQL. Do not assume a failed SQL query licenses a different interpretation of the question."
CATALOG = [
    {
        "intent": "adjacent_ranked_row_positions",
        "claim": "The host can recover two individually requested adjacent row positions by preserving the existing population, projections and ordering in one two-row slice. Select listed_row_positions only if the question explicitly requests precisely the two supplied one-based row positions in that order. Rank positions here count rows, not distinct score levels or tied groups. Return other_or_ambiguous for top-N sets, all ties, distinct metric levels, global output limits, conflicting ranking direction or uncertain population. ranking_matches_question is independently true only when the SQL ordering metric and direction match the stated request. Return the positions named by the question, never copy positions solely from SQL. Cite the exact requested-rank clause. The host checks positions and executes each singleton and the combined slice, preserving only exact agreement.",
    }
]
SCHEMA = {
    "type": "object",
    "properties": {
        "request_kind": {
            "type": "string",
            "enum": ["listed_row_positions", "other_or_ambiguous"],
        },
        "ranking_matches_question": {"type": "boolean"},
        "positions": {"type": "array", "items": {"type": "integer"}},
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "request_kind",
        "ranking_matches_question",
        "positions",
        "source_excerpt",
    ],
    "additionalProperties": False,
}


def route_ranked_union(q, sql, d, a, callback=None):
    plan = plan_ranked_union(sql, d)
    if plan is None:
        raise ValueError("Unsupported ranked union")
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": CATALOG,
            "parsed_sql_facts": {
                "one_based_positions": list(plan.positions),
                "same_population_and_ordering": True,
                "rows_per_branch": 1,
            },
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = a.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="ranked_union_routing",
        temperature=0.0,
        max_tokens=900,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ranked_union",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )
    raw = response.get("response", "")
    if callback:
        callback(
            prompt=prompt,
            response=raw,
            tokens=response.get("tokens_used", 0),
            latency_ms=(perf_counter() - started) * 1000,
            model=response.get("model", "unknown"),
        )
    v = json.loads(raw)
    pos = v.get("positions")
    ex = v.get("source_excerpt")
    activate = (
        v.get("request_kind") == "listed_row_positions"
        and v.get("ranking_matches_question") is True
        and isinstance(pos, list)
        and all(type(n) is int for n in pos)
        and tuple(pos) == plan.positions
        and isinstance(ex, str)
        and len(ex.strip()) >= 4
        and ex in q
    )
    return {
        "activate": activate,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
