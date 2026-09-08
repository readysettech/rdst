"""Restore independently requested linked occurrences by removing key deduplication."""

from dataclasses import dataclass
from decimal import Decimal
import math
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only
from features.ask.calendar_day import structural_schema
from features.ask.matched_percentage import (
    _sql_atom,
    _conjunction,
    accepts_request,
    route_matched_percentage,
)

VERSION = "proved-linked-occurrence-percentage-v4-redundant-distinct"


@dataclass(frozen=True)
class OccurrencePercentagePlan:
    original_sql: str
    candidate_sql: str
    proof_sql: str
    facts: dict


def _value(node):
    while isinstance(node, (exp.Alias, exp.Paren)):
        node = node.this
    return node


def _literal(node, value):
    return (
        isinstance(node, exp.Literal)
        and not node.is_string
        and node.this in {str(value), str(value) + ".0"}
    )


def plan_occurrence_percentage(sql, dialect, schema):
    if dialect != "mysql" or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    t = statements[0]
    if len(t.expressions) != 1 or len(list(t.find_all(exp.Select))) != 2:
        return None
    if any(
        t.args.get(k)
        for k in (
            "where",
            "distinct",
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "with_",
            "qualify",
            "into",
            "locks",
        )
    ):
        return None
    div = _value(t.expressions[0])
    if (
        not isinstance(div, exp.Div)
        or not isinstance(div.expression, exp.Count)
        or div.expression.expressions
    ):
        return None
    denominator = div.expression.this
    distinct_denominator = isinstance(denominator, exp.Distinct)
    if not isinstance(denominator, (exp.Star, exp.Distinct)):
        return None
    cast = _value(div.this)
    if (
        not isinstance(cast, exp.Cast)
        or cast.args["to"].this != exp.DataType.Type.DOUBLE
    ):
        return None
    mul = _value(cast.this)
    if not isinstance(mul, exp.Mul):
        return None
    if _literal(mul.this, 100):
        summed = _value(mul.expression)
    elif _literal(mul.expression, 100):
        summed = _value(mul.this)
    else:
        return None
    if not isinstance(summed, exp.Sum) or not isinstance(summed.this, exp.Case):
        return None
    case = summed.this
    ifs = case.args.get("ifs", [])
    if (
        case.this is not None
        or len(ifs) != 1
        or not _literal(ifs[0].args.get("true"), 1)
        or not _literal(case.args.get("default"), 0)
    ):
        return None
    condition = ifs[0].this
    from_ = t.args.get("from_")
    joins = t.args.get("joins") or []
    if from_ is None or not isinstance(from_.this, exp.Subquery) or len(joins) != 1:
        return None
    derived = from_.this
    inner = derived.this
    if (
        not derived.alias
        or not isinstance(inner, exp.Select)
        or len(inner.expressions) != 1
    ):
        return None
    distinct = inner.args.get("distinct")
    if not isinstance(distinct, exp.Distinct) or any(distinct.args.values()):
        return None
    if any(
        inner.args.get(k)
        for k in (
            "joins",
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "with_",
            "qualify",
            "into",
            "locks",
        )
    ):
        return None
    inner_from = inner.args.get("from_")
    foreign = inner.expressions[0]
    join = joins[0]
    right = join.this
    if (
        inner_from is None
        or not isinstance(inner_from.this, exp.Table)
        or not isinstance(foreign, exp.Column)
        or foreign.is_star
        or not isinstance(right, exp.Table)
    ):
        return None
    left = inner_from.this
    if (
        any(z.db or z.catalog for z in (left, right))
        or left.name.casefold() == right.name.casefold()
    ):
        return None
    if (
        join.side
        or join.kind not in ("", "INNER")
        or join.args.get("using")
        or join.args.get("method")
    ):
        return None
    if foreign.table and foreign.table.casefold() != left.alias_or_name.casefold():
        return None
    if right.alias_or_name.casefold() == derived.alias.casefold():
        return None
    tables = {k.casefold(): v for k, v in schema.tables.items()}
    if any(z.name.casefold() not in tables for z in (left, right)):
        return None
    li, ri = tables[left.name.casefold()], tables[right.name.casefold()]
    lc = {k.casefold(): v for k, v in li.columns.items()}
    rc = {k.casefold(): v for k, v in ri.columns.items()}
    if foreign.name.casefold() not in lc:
        return None
    keys = [name for name, c in ri.columns.items() if c.is_primary_key]
    if len(keys) != 1:
        return None
    integer_types = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}

    def integer_column(c):
        return str(c.data_type).lower().split("(", 1)[0].split()[0] in integer_types

    if not integer_column(lc[foreign.name.casefold()]) or not integer_column(
        rc[keys[0].casefold()]
    ):
        return None
    key = exp.column(keys[0], table=right.alias_or_name, quoted=True)

    def same(a, b):
        return (
            isinstance(a, exp.Column)
            and isinstance(b, exp.Column)
            and (a.table.casefold(), a.name.casefold())
            == (b.table.casefold(), b.name.casefold())
        )

    if distinct_denominator and (
        len(denominator.expressions) != 1 or not same(denominator.expressions[0], key)
    ):
        return None
    on = join.args.get("on")
    link = exp.column(foreign.name, table=derived.alias)
    if not isinstance(on, exp.EQ) or not (
        same(on.this, key)
        and same(on.expression, link)
        or same(on.expression, key)
        and same(on.this, link)
    ):
        return None
    membership = _sql_atom(condition, {right.alias_or_name.casefold(): right.name})
    if membership is None or membership["column"].casefold() not in rc:
        return None
    context = []
    redundant = []
    where = inner.args.get("where")
    for term in _conjunction(where.this) if where else []:
        atom = _sql_atom(
            term, {left.alias_or_name.casefold(): left.name, "": left.name}
        )
        if atom is None or atom["column"].casefold() not in lc:
            return None
        if (
            atom["operator"] == "is_not_null"
            and atom["column"].casefold() == foreign.name.casefold()
        ):
            redundant.append(term.sql(dialect="mysql"))
        else:
            context.append(atom)
    if len(context) > 8:
        return None
    candidate = t.copy()
    candidate.args["from_"].this.this.set("distinct", None)
    if distinct_denominator:
        _value(candidate.expressions[0]).expression.set("this", exp.Star())
    proof = candidate.copy()
    hit_key = exp.Case(
        ifs=[exp.If(this=condition.copy(), true=key.copy())], default=exp.Null()
    )
    proof.set(
        "expressions",
        [
            exp.Count(this=exp.Distinct(expressions=[key.copy()])),
            exp.Count(this=exp.Distinct(expressions=[hit_key])),
            exp.Count(this=exp.Star()),
            summed.copy(),
        ],
    )
    if distinct_denominator:
        for count in (
            exp.Count(this=exp.Star()),
            exp.Count(this=exp.Distinct(expressions=[key.copy()])),
        ):
            query = exp.select(count).from_(right.copy())
            proof.append("expressions", exp.Subquery(this=query))
    facts = {
        "left_source": left.name,
        "right_source": right.name,
        "membership_atom": membership,
        "context_atoms": context,
        "complete_structural_schema": structural_schema(schema),
        "redundant_nonnull_key_conditions": redundant,
        "existing_scale": 100,
    }
    return OccurrencePercentagePlan(
        sql, candidate.sql(dialect="mysql"), proof.sql(dialect="mysql"), facts
    )


def prove_occurrence_percentage(before, rows):
    if len(rows) == 1 and len(rows[0]) == 6:
        total_keys, unique_keys = rows[0][4:]
        if (
            type(total_keys) is not int
            or type(unique_keys) is not int
            or not 0 < total_keys == unique_keys <= 2**53
        ):
            return None
        rows = [rows[0][:4]]
    if (
        len(before) != 1
        or len(before[0]) != 1
        or type(before[0][0]) is not float
        or not math.isfinite(before[0][0])
        or len(rows) != 1
        or len(rows[0]) != 4
    ):
        return None
    old_total, old_hits, total, hits = rows[0]
    if any(type(v) is not int for v in (old_total, old_hits, total)):
        return None
    if not (
        type(hits) is int
        or isinstance(hits, Decimal)
        and hits.is_finite()
        and hits == hits.to_integral_value()
    ):
        return None
    hits = int(hits)
    if (
        not 0 < old_total < total <= 2**53
        or not 0 <= old_hits <= old_total
        or not old_hits <= hits <= total
        or hits * 100 > 2**53
    ):
        return None
    old_expected = float(old_hits * 100) / float(old_total)
    expected = float(hits * 100) / float(total)
    if before[0][0] != old_expected or expected == old_expected:
        return None
    return {
        "original_entity_count": old_total,
        "original_hits": old_hits,
        "occurrence_count": total,
        "occurrence_hits": hits,
        "original_percentage": old_expected,
        "expected_percentage": expected,
    }


def accepts_occurrence_percentage(proof, rows):
    return (
        proof is not None
        and len(rows) == 1
        and len(rows[0]) == 1
        and type(rows[0][0]) is float
        and math.isfinite(rows[0][0])
        and rows[0][0] == proof["expected_percentage"]
    )


from features.ask.percentage_roles import (
    route_percentage_roles as route_occurrence_percentage,
)


def has_percentage_authorization(question, sql, diagnostic):
    """Require the prior retained router's applied percentage decision for this SQL."""
    import hashlib
    from features.ask.correction_intent_state import selected_correction_intents

    if not isinstance(diagnostic, dict):
        return False
    if (
        diagnostic.get("shadow") is not False
        or diagnostic.get("selected_sql_applied") is not True
        or diagnostic.get("router_called_before_application") is not True
    ):
        return False
    if "percentage_output" not in selected_correction_intents(diagnostic):
        return False
    if (
        diagnostic.get("effective_question_sha256")
        != hashlib.sha256(question.encode()).hexdigest()
    ):
        return False
    if (
        diagnostic.get("selected_sql_sha256")
        != hashlib.sha256(sql.encode()).hexdigest()
    ):
        return False
    return True
