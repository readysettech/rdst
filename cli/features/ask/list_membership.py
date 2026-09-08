"""Correct existential membership only after proving bounded list storage."""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
from time import perf_counter
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only

VERSION = "proved-comma-list-other-member-v7-null-empty-split"
MAX_VALUES = 1000


def token(value):
    # Storage grammar only. User intent is classified by GLM.
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9]+(?:[ -][A-Za-z0-9]+)*", value) is not None
        and len(value) <= 64
    )


def unparen(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def conjuncts(node):
    node = unparen(node)
    return (
        conjuncts(node.this) + conjuncts(node.expression)
        if isinstance(node, exp.And)
        else [node]
    )


@dataclass(frozen=True)
class ListPlan:
    candidate_sql: str
    proof_sql: str
    column: str
    target: str
    predicate_kind: str = "negative_like"


def set_function(node):
    return (
        isinstance(node, exp.Anonymous)
        and node.name.upper() == "FIND_IN_SET"
        and len(node.expressions) == 2
    )


def zero(node):
    return isinstance(node, exp.Literal) and not node.is_string and node.this == "0"


def absence_predicate(node):
    node = unparen(node)
    if isinstance(node, exp.Like) and node.args.get("negate"):
        col, literal = node.this, node.expression
        if (
            isinstance(col, exp.Column)
            and isinstance(literal, exp.Literal)
            and literal.is_string
        ):
            value = literal.this
            if value.startswith("%") and value.endswith("%") and token(value[1:-1]):
                return col, value[1:-1], "negative_like"
        return None
    call = None
    if isinstance(node, exp.Not):
        inner = unparen(node.this)
        if set_function(inner):
            call = inner
        elif (
            isinstance(inner, exp.GT)
            and set_function(inner.this)
            and zero(inner.expression)
        ):
            call = inner.this
    elif isinstance(node, exp.EQ) and set_function(node.this) and zero(node.expression):
        call = node.this
    if call is None:
        return None
    literal, col = call.expressions
    if (
        not isinstance(literal, exp.Literal)
        or not literal.is_string
        or not token(literal.this)
        or not isinstance(col, exp.Column)
    ):
        return None
    return col, literal.this, "set_absence"


def plan_list_membership(sql, dialect):
    if dialect != "mysql" or "\\" in sql or not check_read_only(sql)["is_read_only"]:
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
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "with_",
            "distinct",
            "qualify",
            "locks",
            "into",
        ]
    ):
        return None
    output = tree.expressions[0]
    output = output.this if isinstance(output, exp.Alias) else output
    if (
        not isinstance(output, exp.Count)
        or not isinstance(output.this, exp.Star)
        or output.args.get("expressions")
    ):
        return None
    tables = list(tree.find_all(exp.Table))
    if (
        len(tables) != 1
        or tables[0].db
        or tables[0].catalog
        or not tree.args.get("where")
    ):
        return None
    if any(
        isinstance(n, exp.Func)
        and not isinstance(n, (exp.Count, exp.And, exp.Or, exp.Not))
        and not set_function(n)
        for n in tree.walk()
    ):
        return None
    matches = []
    clauses = conjuncts(tree.args["where"].this)
    for index, clause in enumerate(clauses):

        def alternatives_of(node):
            node = unparen(node)
            if isinstance(node, exp.Or):
                return alternatives_of(node.this) + alternatives_of(node.expression)
            return [node]

        alternatives = alternatives_of(clause)
        if len(alternatives) > 3:
            continue
        found = [
            (node, absence_predicate(node))
            for node in alternatives
            if absence_predicate(node) is not None
        ]
        if len(found) != 1:
            continue
        selected, (col, value, predicate_kind) = found[0]
        if len(alternatives) == 2:
            other = alternatives[0] if alternatives[1] is selected else alternatives[1]
            if (
                not isinstance(other, exp.Is)
                or other.this != col
                or not isinstance(other.expression, exp.Null)
            ):
                continue
        if len(alternatives) == 3:
            others = [n for n in alternatives if n is not selected]
            nulls = [
                n
                for n in others
                if isinstance(n, exp.Is)
                and n.this == col
                and isinstance(n.expression, exp.Null)
            ]
            empties = [
                n
                for n in others
                if isinstance(n, exp.EQ)
                and n.this == col
                and isinstance(n.expression, exp.Literal)
                and n.expression.is_string
                and n.expression.this == ""
            ]
            if predicate_kind != "set_absence" or len(nulls) != 1 or len(empties) != 1:
                continue
            predicate_kind = "null_empty_set_absence"
        matches.append((index, clause, col, value, predicate_kind))
    if len(matches) != 1:
        return None
    index, old, col, target, predicate_kind = matches[0]
    # No additional OR/NOT/LIKE or predicates on the same list column.
    remaining = [c for i, c in enumerate(clauses) if i != index]
    if any(
        isinstance(n, (exp.Or, exp.Not, exp.Like, exp.Anonymous))
        or isinstance(n, exp.Column)
        and n.name == col.name
        for c in remaining
        for n in c.walk()
    ):
        return None
    candidate_predicate = exp.NEQ(
        this=col.copy(), expression=exp.Literal.string(target)
    )
    candidate = tree.copy()
    candidate.set(
        "where",
        exp.Where(
            this=exp.and_(
                *[
                    c.copy() if i != index else candidate_predicate.copy()
                    for i, c in enumerate(clauses)
                ]
            )
        ),
    )
    proof = tree.copy()
    binary_col = exp.Cast(this=col.copy(), to=exp.DataType.build("BINARY"))

    def summed(predicate):
        return exp.Sum(
            this=exp.Case(
                ifs=[exp.If(this=predicate.copy(), true=exp.Literal.number(1))],
                default=exp.Literal.number(0),
            )
        )

    proof.set(
        "expressions",
        [
            binary_col.copy(),
            exp.Count(this=exp.Star()),
            summed(old),
            summed(candidate_predicate),
        ],
    )
    proof.set(
        "where",
        exp.Where(this=exp.and_(*[c.copy() for c in remaining])) if remaining else None,
    )
    proof.set("group", exp.Group(expressions=[binary_col.copy()]))
    proof.set("limit", exp.Limit(expression=exp.Literal.number(MAX_VALUES + 1)))
    return ListPlan(
        candidate.sql(dialect="mysql"),
        proof.sql(dialect="mysql"),
        col.sql(dialect="mysql"),
        target,
        predicate_kind,
    )


def integer(value):
    if isinstance(value, Decimal) and not value.is_finite():
        return None
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return int(value) if value == int(value) and 0 <= value < 2**53 else None


def proved_other_count(plan, original, rows):
    if len(original) != 1 or len(original[0]) != 1 or not 1 <= len(rows) <= MAX_VALUES:
        return None
    old_total = new_total = 0
    singleton = mixed = False
    seen = set()
    canonical = {plan.target.lower(): plan.target}
    for row in rows:
        if len(row) != 4:
            return None
        value, n, old, new = row
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return None
        n, old, new = map(integer, [n, old, new])
        if (
            n is None
            or n == 0
            or old not in (0, n)
            or new not in (0, n)
            or value in seen
        ):
            return None
        seen.add(value)
        if value is None:
            expected = False
        else:
            if not isinstance(value, str) or len(value) > 512:
                return None
            members = value.split(",")
            if not all(token(m) for m in members) or len(set(members)) != len(members):
                return None
            for member in members:
                if canonical.setdefault(member.lower(), member) != member:
                    return None
            # Ambiguous casing or collation differences abstain below; no fuzzy matching.
            expected = any(m != plan.target for m in members)
            singleton |= members == [plan.target]
            mixed |= plan.target in members and len(members) > 1
        if new != n * expected:
            return None
        old_total += old
        new_total += new
        if max(old_total, new_total) >= 2**53:
            return None
    if (
        not singleton
        or not mixed
        or integer(original[0][0]) != old_total
        or new_total == old_total
    ):
        return None
    return new_total


def accepts_other_count(expected, rows):
    return (
        expected is not None
        and len(rows) == 1
        and len(rows[0]) == 1
        and integer(rows[0][0]) == expected
    )


SYSTEM = "Interpret the effective question independently of generated SQL. Classify membership intent in any language, including typos. The host can change only a negative substring predicate, never output shape or other scope. Return the required JSON object; do not generate SQL."
CATALOG = [
    {
        "intent": "other_member_of_collection",
        "claim": "Classify quantifier as any_other_member when a collection must contain at least one member different from the named member; excludes_named_member when that member must be absent; whole_value_different for comparison of an entire scalar/list string; unknown otherwise. Independently report requires_named_member: true for 'in addition to X', 'both X and another' or any requirement to also contain X, false otherwise. The host cannot add that requirement. Report requests_missing_values true only when missing/unknown collections are explicitly requested; otherwise false because existence of another member needs a known member. Classify output_request as count_parent_records only when the question asks how many parent objects or rows carry a qualifying member. Use count_collection_members when the question asks how many tags, skills or other members occur across those objects; COUNT(*) over parent rows cannot answer that. Use entity for identities, other otherwise. Classify value_role as collection_member for tags, skills, subtypes or comparable sets; scalar_text for descriptions, titles, addresses or scalar labels; unknown if unclear. Copy the named member and an excerpt of its condition from the question. Finally classify generated_column_matches_question as yes only if the SQL predicate column denotes the same collection requested in the question, no for a different concept, unknown if ambiguous. Do not use SQL to fill in a member absent from the question. Independently evaluate a hypothetical record that has both the named member and one different member: mixed_members_match is yes if it satisfies the question's member condition, no if it violates it, and unknown if unclear. An existential request for another skill/tag/member alone permits that record; a prohibition on records carrying the named member rejects it even if another phrase says any tags. Keep this counterexample independent of the quantifier label.",
    }
]
SCHEMA = {
    "type": "object",
    "properties": {
        "mixed_members_match": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "quantifier": {
            "type": "string",
            "enum": [
                "any_other_member",
                "excludes_named_member",
                "whole_value_different",
                "unknown",
            ],
        },
        "requires_named_member": {"type": "boolean"},
        "requests_missing_values": {"type": "boolean"},
        "output_request": {
            "type": "string",
            "enum": [
                "count_parent_records",
                "count_collection_members",
                "entity",
                "other",
            ],
        },
        "value_role": {
            "type": "string",
            "enum": ["collection_member", "scalar_text", "unknown"],
        },
        "generated_column_matches_question": {
            "type": "string",
            "enum": ["yes", "no", "unknown"],
        },
        "named_member": {"type": "string"},
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "mixed_members_match",
        "quantifier",
        "requires_named_member",
        "requests_missing_values",
        "output_request",
        "value_role",
        "generated_column_matches_question",
        "named_member",
        "source_excerpt",
    ],
    "additionalProperties": False,
}


def route_list_membership(q, sql, d, a, callback=None):
    plan = plan_list_membership(sql, d)
    if plan and plan.predicate_kind == "null_empty_set_absence":
        from .membership_request import route_qualified_membership

        return route_qualified_membership(
            q,
            plan,
            d,
            a,
            lambda: _route_list_membership(q, sql, d, a, callback, question_only=True),
            callback,
        )
    return _route_list_membership(q, sql, d, a, callback)


def _route_list_membership(q, sql, d, a, callback=None, *, question_only=False):
    plan = plan_list_membership(sql, d)
    catalog = CATALOG
    system = SYSTEM
    if plan and plan.predicate_kind == "set_absence":
        system = (
            "Interpret the effective question independently of generated SQL. "
            "Classify membership intent in any language, including typos. "
            "The host can change only a test for absence of a collection member, "
            "never its column, output shape or other scope. "
            "Before approving column correspondence, compare the collection "
            "requested by the question with the predicate column's meaning. "
            "Different collection concepts are not interchangeable merely "
            "because both can store comma-separated values. If their equivalence "
            "is not established, use no or unknown. "
            "Interpret spelling mistakes, but preserve them when copying "
            "source_excerpt: copy exact consecutive characters from the question, "
            "without translating, correcting spelling or substituting SQL names. "
            "Return only the required JSON object; do not generate SQL."
        )
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": catalog,
            "structure_facts": {
                "predicate_column": plan.column,
                "predicate_member": plan.target,
            }
            if plan
            else {},
        },
        ensure_ascii=False,
    )
    if question_only:
        payload = json.loads(prompt)
        payload.pop("generated_sql")
        prompt = json.dumps(payload, ensure_ascii=False)
    started = perf_counter()
    r = a.generate_response(
        prompt=prompt,
        system_message=system,
        purpose="list_membership_routing",
        temperature=0.0,
        max_tokens=1000,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "list_membership",
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
            latency_ms=(perf_counter() - started) * 1000,
            model=r.get("model", "unknown"),
        )
    v = json.loads(raw)
    excerpt, member = v.get("source_excerpt"), v.get("named_member")
    active = (
        bool(plan)
        and v.get("mixed_members_match") == "yes"
        and v.get("quantifier") == "any_other_member"
        and v.get("value_role") == "collection_member"
        and v.get("requests_missing_values") is False
        and v.get("requires_named_member") is False
        and v.get("output_request") == "count_parent_records"
        and v.get("generated_column_matches_question") == "yes"
        and isinstance(excerpt, str)
        and bool(excerpt.strip())
        and excerpt in q
        and isinstance(member, str)
        and bool(member)
        and member in q
        and member.lower() == plan.target.lower()
    )
    return {
        "activate": active,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": r.get("model"),
    }
