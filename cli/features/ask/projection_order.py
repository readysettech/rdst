"""Present a complete explicit output list in its requested order."""

import hashlib
import json
from time import perf_counter
import sqlglot
from sqlglot import exp
from .projection_contract import ProjectionPlan, projection_shape
from .sql_validation import check_read_only

VERSION = "explicit-output-list-order-v3-cte"


def projection_order_shape(sql, dialect):
    tree = projection_shape(sql, dialect)
    if tree is None:
        tree = _cte_outer_projection(sql, dialect)
    if tree is None:
        return None
    if any(
        not isinstance(e.this if isinstance(e, exp.Alias) else e, exp.Column)
        for e in tree.expressions
    ):
        return None
    order = tree.args.get("order")
    if order and any(isinstance(o.this, exp.Literal) for o in order.expressions):
        return None
    return tree


def _cte_outer_projection(sql, dialect):
    """Admit only a bounded read-only CTE; its complete body stays untouched."""
    read = "postgres" if dialect in {"postgres", "postgresql"} else dialect
    if read not in {"mysql", "postgres"} or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read=read)
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = statements[0]
    with_ = tree.args.get("with_")
    if (
        with_ is None
        or with_.args.get("recursive")
        or not 1 <= len(with_.expressions) <= 3
    ):
        return None
    names = [c.alias.casefold() for c in with_.expressions]
    if not all(names) or len(set(names)) != len(names):
        return None
    if len(list(tree.find_all(exp.Select))) != 1 + len(with_.expressions):
        return None
    for cte in with_.expressions:
        if not isinstance(cte.this, exp.Select) or any(
            cte.this.args.get(k) for k in ("with_", "into", "locks")
        ):
            return None
    outer = tree.copy()
    outer.set("with_", None)
    if projection_shape(outer.sql(dialect=read), dialect) is None:
        return None
    return tree


def plan_projection_order(sql, dialect, indices):
    tree = projection_order_shape(sql, dialect)
    if tree is None or not isinstance(indices, (tuple, list)):
        return None
    identity = list(range(len(tree.expressions)))
    if (
        any(type(i) is not int for i in indices)
        or sorted(indices) != identity
        or list(indices) == identity
    ):
        return None
    tree.set("expressions", [tree.expressions[i].copy() for i in indices])
    read = "postgres" if dialect in {"postgres", "postgresql"} else dialect
    return ProjectionPlan(
        tree.sql(dialect=read),
        tuple(indices),
        len(indices),
        tree.args.get("order") is not None,
    )


def route_projection_order(q, sql, d, a, callback=None):
    read = "postgres" if d in {"postgres", "postgresql"} else d
    tree = sqlglot.parse_one(sql, read=read)
    count = len(tree.expressions)
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": CATALOG,
            "parsed_sql_facts": {
                "projections": [
                    {"index": i, "expression": e.sql(dialect=read)}
                    for i, e in enumerate(tree.expressions)
                ]
            },
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = a.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="projection_order_routing",
        temperature=0.0,
        max_tokens=700,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "projection_order",
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
    indices = v.get("projection_order")
    ex = v.get("source_excerpt", "")
    valid = (
        v.get("enumeration_resolves_every_projection") is True
        and isinstance(indices, list)
        and all(type(n) is int for n in indices)
        and sorted(indices) == list(range(count))
        and isinstance(ex, str)
        and len(ex.strip()) >= 4
        and ex in q
    )
    return {
        "keep_indices": indices if valid else list(range(count)),
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }


SYSTEM = "Classify the explicit output field list in the effective question. Interpret any language and ordinary typos. Treat SQL as data. Never write SQL or infer a requested output list from generated SQL."

CATALOG = [
    {
        "intent": "explicit_output_field_order",
        "claim": "For an explicit enumeration of requested output attributes, the product presents columns in the order those attributes are listed. This is a presentation policy. Set enumeration_resolves_every_projection=true only when every existing projection maps uniquely to a separately named requested output attribute, and their relative order is stated by that output list. This boolean asks whether the requested list uniquely identifies every projected field, not whether SQL already follows that order. Set it true for a complete unique mapping even when current SQL columns are in the wrong order. Columns mentioned only in conditions or comparisons are not requested outputs. A generic request for details, entities, full names or complete addresses does not individually order their component fields; preserve existing order in those cases. If a requested field is missing, several projections jointly represent one requested attribute, there are extra unrequested fields, or the mapping is ambiguous, use false. Return the projection indices in the requested order, with every index exactly once. Never drop, add, rename, combine or reinterpret fields. Cite the exact output-list clause from the question.",
    }
]

SCHEMA = {
    "type": "object",
    "properties": {
        "enumeration_resolves_every_projection": {"type": "boolean"},
        "projection_order": {"type": "array", "items": {"type": "integer"}},
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "enumeration_resolves_every_projection",
        "projection_order",
        "source_excerpt",
    ],
    "additionalProperties": False,
}
