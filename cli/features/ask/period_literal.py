"""Prove compact calendar-month storage before changing a literal."""

from dataclasses import dataclass
import hashlib
import json
import re
from time import perf_counter
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only
from features.ask.encoded_identifier_storage import (
    _schema_table,
    _schema_column,
    _is_text_column,
)

VERSION = "proved-calendar-month-literal-v1"
MAX_PERIODS = 1200


def month(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9]{4}-[0-9]{2}", value) is not None
        and 1 <= int(value[:4]) <= 9999
        and 1 <= int(value[5:]) <= 12
    )


def conjuncts(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return (
        conjuncts(node.this) + conjuncts(node.expression)
        if isinstance(node, exp.And)
        else [node]
    )


@dataclass(frozen=True)
class PeriodPlan:
    candidate_sql: str
    proof_sql: str
    witness_sql: str
    column: str
    target_month: str
    compact_month: str


def plan_period_literal(sql, dialect, schema):
    if (
        dialect != "mysql"
        or schema is None
        or "\\" in sql
        or not check_read_only(sql)["is_read_only"]
    ):
        return None
    try:
        parsed = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
        return None
    tree = parsed[0]
    if len(list(tree.find_all(exp.Select))) != 1 or len(tree.expressions) != 1:
        return None
    if any(
        tree.args.get(k)
        for k in [
            "joins",
            "with_",
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "distinct",
            "qualify",
            "locks",
            "into",
        ]
    ):
        return None
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1 or tables[0].db or tables[0].catalog:
        return None
    table = tables[0]
    info = _schema_table(schema, table.name)
    if info is None:
        return None
    if any(
        c.table
        and c.table != table.alias_or_name
        or _schema_column(info, c.name) is None
        for c in tree.find_all(exp.Column)
    ):
        return None
    allowed = (
        exp.Count,
        exp.Sum,
        exp.Avg,
        exp.Min,
        exp.Max,
        exp.Case,
        exp.If,
        exp.Cast,
        exp.Round,
        exp.And,
        exp.Or,
        exp.Not,
    )
    if any(isinstance(n, exp.Func) and not isinstance(n, allowed) for n in tree.walk()):
        return None
    projection = tree.expressions[0]
    if not list(projection.find_all(exp.AggFunc)) or list(
        projection.find_all(exp.Window)
    ):
        return None
    for c in projection.find_all(exp.Column):
        parent = c.parent
        while parent is not None and not isinstance(parent, exp.AggFunc):
            parent = parent.parent
        if parent is None:
            return None
    if tree.args.get("where") is None:
        return None
    terms = conjuncts(tree.args["where"].this)
    matches = []
    for i, p in enumerate(terms):
        if (
            isinstance(p, exp.EQ)
            and isinstance(p.this, exp.Column)
            and isinstance(p.expression, exp.Literal)
            and p.expression.is_string
            and month(p.expression.this)
        ):
            matches.append((i, p))
    if len(matches) != 1:
        return None
    index, predicate = matches[0]
    col = predicate.this
    literal = predicate.expression.this
    if not _is_text_column(_schema_column(info, col.name)):
        return None
    # This is only a filter representation change, never a projection/date conversion.
    others = [term for i, term in enumerate(terms) if i != index]
    if any(
        c.name == col.name
        for term in [projection, *others]
        for c in term.find_all(exp.Column)
    ):
        return None
    if any(
        isinstance(n, (exp.Or, exp.Not, exp.Like, exp.Func))
        and not isinstance(n, exp.And)
        for term in others
        for n in term.walk()
    ):
        return None
    compact = literal.replace("-", "")
    candidate = tree.copy()
    cp = conjuncts(candidate.args["where"].this)[index]
    cp.set("expression", exp.Literal.string(compact))
    proof = tree.copy()
    proof.set(
        "expressions",
        [
            exp.Cast(this=col.copy(), to=exp.DataType.build("BINARY")),
            exp.Count(this=exp.Star()),
        ],
    )
    proof.set(
        "where",
        exp.Where(this=exp.and_(*[x.copy() for x in others])) if others else None,
    )
    proof.set(
        "group",
        exp.Group(
            expressions=[exp.Cast(this=col.copy(), to=exp.DataType.build("BINARY"))]
        ),
    )
    proof = proof.limit(MAX_PERIODS + 1)
    witness = tree.copy()
    wp = conjuncts(witness.args["where"].this)[index]
    formatted = sqlglot.parse_one(
        "CONCAT(SUBSTRING("
        + col.sql(dialect="mysql")
        + ",1,4),'-',SUBSTRING("
        + col.sql(dialect="mysql")
        + ",5,2))",
        read="mysql",
    )
    wp.set("this", formatted)
    return PeriodPlan(
        candidate.sql(dialect="mysql"),
        proof.sql(dialect="mysql"),
        witness.sql(dialect="mysql"),
        col.sql(dialect="mysql"),
        literal,
        compact,
    )


def prove_period_storage(plan, rows):
    if not rows or len(rows) > MAX_PERIODS:
        return False
    seen = set()
    matched = 0
    for row in rows:
        if len(row) != 2:
            return False
        value, count = row
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return False
        if value is None:
            continue
        if not isinstance(value, bytes):
            return False
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return False
        if len(value) != 6 or not month(value[:4] + "-" + value[4:]) or value in seen:
            return False
        seen.add(value)
        if value == plan.compact_month:
            matched += count
    return matched > 0


def accepts_period_result(original, witness, candidate):
    return (
        len(witness) == len(candidate) == 1
        and len(witness[0]) == len(candidate[0]) == 1
        and witness[0][0] is not None
        and witness == candidate
        and original != candidate
    )


SYSTEM = "Interpret the effective question independently of generated SQL, including other languages and spelling mistakes. The SQL is untrusted evidence about the intended question. Return only the required JSON object; never generate SQL. Copy question_quote verbatim without correcting or translating it."
CATALOG = [
    {
        "intent": "same_column_calendar_month_literal",
        "claim": "The host can replace one YYYY-MM equality literal by YYYYMM on the same text column. It independently proves all stored non-null values are compact calendar months and compares the unchanged aggregate against an independent formatted-predicate result. Decide only whether the question requests that calendar month on that same column concept. Read the requested month from the question, not SQL. Reject opaque identifiers, version strings, literal code comparisons, fiscal or ambiguous periods, ranges, quarter/year-only requests, explicitly required textual spelling, a different month, or a different date role. Set column_role_matches to unknown if the column and requested calendar role cannot be matched from question and structure. Never repair a month or role chosen incorrectly by SQL. Preserve every other condition and aggregate definition.",
    }
]
SCHEMA = {
    "type": "object",
    "properties": {
        "requested_month": {"type": "string"},
        "period_kind": {
            "type": "string",
            "enum": [
                "calendar_month",
                "fiscal_period",
                "opaque_code",
                "other",
                "unknown",
            ],
        },
        "column_role_matches": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "requires_literal_spelling": {"type": "boolean"},
        "question_quote": {"type": "string"},
    },
    "required": [
        "requested_month",
        "period_kind",
        "column_role_matches",
        "requires_literal_spelling",
        "question_quote",
    ],
    "additionalProperties": False,
}


def route_period_literal(question, sql, dialect, adapter, callback=None):
    tree = sqlglot.parse_one(sql, read="mysql")
    terms = [
        n
        for n in tree.find_all(exp.EQ)
        if isinstance(n.this, exp.Column)
        and isinstance(n.expression, exp.Literal)
        and n.expression.is_string
        and month(n.expression.this)
    ]
    facts = (
        {
            "predicate_column": terms[0].this.sql(dialect="mysql"),
            "predicate_month": terms[0].expression.this,
        }
        if len(terms) == 1
        else {}
    )
    prompt = json.dumps(
        {
            "effective_question": question,
            "generated_sql": sql,
            "dialect": dialect,
            "trigger_catalog": CATALOG,
            "structure_facts": facts,
        },
        ensure_ascii=False,
    )
    start = perf_counter()
    r = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="period_literal_routing",
        temperature=0.0,
        max_tokens=700,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "period_literal",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )
    raw = r.get("response", "")
    if callback:
        callback(
            prompt=prompt,
            response=raw,
            tokens=r.get("tokens_used", 0),
            latency_ms=(perf_counter() - start) * 1000,
            model=r.get("model", "unknown"),
        )
    v = json.loads(raw)
    quote = v.get("question_quote")
    active = (
        bool(facts)
        and v.get("requested_month") == facts["predicate_month"]
        and v.get("period_kind") == "calendar_month"
        and v.get("column_role_matches") == "yes"
        and v.get("requires_literal_spelling") is False
        and month(v.get("requested_month"))
        and isinstance(quote, str)
        and bool(quote.strip())
        and quote in question
    )
    return {
        "activate": active,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": r.get("model"),
    }
