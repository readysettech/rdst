"""Return native name fields only when complete values reconstruct exactly."""

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from time import perf_counter
import sqlglot
from sqlglot import exp
from .sql_validation import check_read_only

VERSION = "native-name-format-v4-distinct-reconstruction"


@dataclass(frozen=True)
class NameFormatPlan:
    candidate_sql: str
    index: int
    original_columns: int
    separators: tuple[str, ...]
    ordered: bool


def plan_name_format(sql, dialect):
    d = "postgres" if dialect in {"postgres", "postgresql"} else dialect
    if d not in {"mysql", "postgres"} or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        trees = sqlglot.parse(sql, read=d)
    except sqlglot.errors.ParseError:
        return None
    if len(trees) != 1 or not isinstance(trees[0], exp.Select):
        return None
    tree = trees[0]
    if not 1 <= len(tree.expressions) <= 8 or tree.args.get("from_") is None:
        return None
    if any(tree.args.get(k) for k in ("having", "with_", "qualify", "into", "locks")):
        return None
    distinct = tree.args.get("distinct")
    if distinct is not None and (
        not isinstance(distinct, exp.Distinct) or any(distinct.args.values())
    ):
        return None
    matches = [
        (i, e.this if isinstance(e, exp.Alias) else e)
        for i, e in enumerate(tree.expressions)
        if isinstance(e.this if isinstance(e, exp.Alias) else e, exp.Concat)
    ]
    if len(matches) != 1:
        return None
    index, composite = matches[0]
    for node in tree.walk():
        if (
            isinstance(node, exp.Func)
            and node is not composite
            and not isinstance(
                node,
                (
                    exp.And,
                    exp.Or,
                    exp.Not,
                    exp.Sum,
                    exp.Avg,
                    exp.Min,
                    exp.Max,
                    exp.Count,
                ),
            )
        ):
            return None
    parts = list(composite.expressions)
    if len(parts) not in {3, 5, 7}:
        return None
    columns, separators = parts[::2], parts[1::2]
    if not all(isinstance(c, exp.Column) and not c.is_star for c in columns):
        return None
    group = tree.args.get("group")
    if group is not None:
        # Replacing a grouped composite must not add a grouping dimension.
        # Require each native part and adjacent output to be a direct group key.
        if any(value for key, value in group.args.items() if key != "expressions"):
            return None
        keys = list(group.expressions)
        if not keys or any(
            not isinstance(key, exp.Column) or key.is_star for key in keys
        ):
            return None
        adjacent = [
            e.this if isinstance(e, exp.Alias) else e
            for i, e in enumerate(tree.expressions)
            if i != index
        ]
        if any(not isinstance(e, exp.Column) or e.is_star for e in adjacent):
            return None
        if any(column not in keys for column in [*columns, *adjacent]):
            return None
    names = [c.name.casefold() for c in columns]
    if len(set(names)) != len(names):
        return None
    if any(
        e.alias_or_name.casefold() in names
        for i, e in enumerate(tree.expressions)
        if i != index
    ):
        return None
    if not all(
        isinstance(s, exp.Literal)
        and s.is_string
        and len(s.this) <= 8
        and not any(ch.isalnum() for ch in s.this)
        for s in separators
    ):
        return None
    alias = tree.expressions[index].alias
    order = tree.args.get("order")
    if order:
        if any(isinstance(e.this, exp.Literal) for e in order.expressions):
            return None
        if alias and any(
            not c.table and c.name.casefold() == alias.casefold()
            for c in order.find_all(exp.Column)
        ):
            return None
    outputs = tree.expressions
    old_count = len(outputs)
    tree.set(
        "expressions",
        [e.copy() for e in outputs[:index]]
        + [c.copy() for c in columns]
        + [e.copy() for e in outputs[index + 1 :]],
    )
    return NameFormatPlan(
        tree.sql(dialect=d),
        index,
        old_count,
        tuple(s.this for s in separators),
        order is not None,
    )


def accepts_name_format(original, candidate, plan):
    if not 1 <= len(original) <= 10000 or len(original) != len(candidate):
        return False
    if any(
        len(row) != plan.original_columns or not isinstance(row[plan.index], str)
        for row in original
    ):
        return False
    count = len(plan.separators) + 1
    reconstructed = []
    for row in candidate:
        if len(row) != plan.original_columns + count - 1:
            return False
        parts = row[plan.index : plan.index + count]
        if not all(isinstance(p, str) for p in parts):
            return False
        name = parts[0]
        for separator, part in zip(plan.separators, parts[1:]):
            name += separator + part
        reconstructed.append(
            tuple(row[: plan.index]) + (name,) + tuple(row[plan.index + count :])
        )
    expected = [tuple(row) for row in original]
    if plan.ordered:
        return reconstructed == expected
    try:
        return Counter(reconstructed) == Counter(expected)
    except TypeError:
        return False


SYSTEM = "Classify the content and presentation explicitly requested by effective_question. Interpret any language and ordinary typos. Use the SQL expression only to identify its source fields, not to infer a missing request. Classify each property independently. Do not decide whether SQL is correct and do not write SQL."

CATALOG = [
    {
        "intent": "native_name_fields",
        "claim": "Classify requested_human_name_parts as true only if every source column of the supplied composite expression is a human name part requested by the question. The ordinary request for a full human name includes its given, middle and family name parts; a request only for given names does not include family names. Classify explicit_combined_format as true when the question asks for one string or one column, a particular combined representation, exact display text, formatting punctuation, labels, or another presentation requirement that needs the concatenated value. Full name by itself specifies name content, not an explicit string/column constraint. For addresses, identifiers, paths, product codes, uncertain source-field meanings, or non-name composites, requested_human_name_parts must be false. When presentation is unclear, explicit_combined_format must be true. Cite an exact supporting excerpt from the question. The host's general tabular policy retains native source name fields when all name content is requested and combined formatting is not explicit. It must preserve every other output and prove exact recombination to the original strings for every complete result row. It cannot drop content, alter populations, change null behavior, or invent parts.",
    }
]

SCHEMA = {
    "type": "object",
    "properties": {
        "requested_human_name_parts": {"type": "boolean"},
        "explicit_combined_format": {"type": "boolean"},
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "requested_human_name_parts",
        "explicit_combined_format",
        "source_excerpt",
    ],
    "additionalProperties": False,
}


def route_name_format(q, sql, d, a, callback=None):
    started = perf_counter()
    d = "postgres" if d in {"postgres", "postgresql"} else d
    tree = sqlglot.parse_one(sql, read=d)
    from sqlglot import exp

    composite = next(tree.find_all(exp.Concat))
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": CATALOG,
            "parsed_sql_facts": {
                "composite_expression": composite.sql(dialect=d),
                "source_fields": [
                    c.sql(dialect=d) for c in composite.find_all(exp.Column)
                ],
            },
        },
        ensure_ascii=False,
    )
    response = a.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="name_format_routing",
        temperature=0.0,
        max_tokens=700,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "name_format",
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
    ex = v.get("source_excerpt")
    activate = (
        isinstance(ex, str)
        and bool(ex.strip())
        and ex in q
        and v.get("requested_human_name_parts") is True
        and v.get("explicit_combined_format") is False
    )
    return {
        "activate": activate,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
