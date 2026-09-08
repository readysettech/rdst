"""Stabilize an existing top-one result with a proved unique output identity."""

from dataclasses import dataclass
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only

VERSION = "declared-identity-first-v1"


@dataclass(frozen=True)
class StableFirstPlan:
    original_sql: str
    candidate_sql: str
    baseline_rank_sql: str
    candidate_rank_sql: str
    identity: tuple[str, ...]
    output_columns: int
    rank_columns: int


def plan_stable_first(sql, dialect, schema):
    if schema is None:
        return None
    if dialect not in {"mysql", "postgres"} or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = statements[0]
    if len(list(tree.find_all(exp.Select))) != 1:
        return None
    if any(
        tree.args.get(k)
        for k in [
            "group",
            "having",
            "distinct",
            "with_",
            "qualify",
            "offset",
            "locks",
            "into",
        ]
    ):
        return None
    limit = tree.args.get("limit")
    order = tree.args.get("order")
    source = tree.args.get("from_")
    if (
        limit is None
        or limit.expression != exp.Literal.number(1)
        or order is None
        or source is None
        or not isinstance(source.this, exp.Table)
    ):
        return None
    if not 1 <= len(tree.expressions) <= 8 or not 1 <= len(order.expressions) <= 4:
        return None
    tables = list(tree.find_all(exp.Table))
    if not 1 <= len(tables) <= 4 or any(
        t.db
        or t.catalog
        or any(v for k, v in t.args.items() if k not in {"this", "alias"})
        for t in tables
    ):
        return None
    aliases = {t.alias_or_name.casefold(): t.name for t in tables}
    if len(aliases) != len(tables):
        return None
    for join in tree.args.get("joins", []):
        if (
            join.args.get("side")
            or join.args.get("kind") not in (None, "", "INNER")
            or join.args.get("method")
            or join.args.get("using")
            or join.args.get("on") is None
        ):
            return None
    output = [e.this if isinstance(e, exp.Alias) else e for e in tree.expressions]
    ranks = [e.this for e in order.expressions]
    if any(
        not isinstance(c, exp.Column) or c.is_star or not c.table
        for c in output + ranks
    ):
        return None
    if len({c.table.casefold() for c in output}) != 1:
        return None
    for c in tree.find_all(exp.Column):
        table = schema.tables.get(aliases.get(c.table.casefold(), ""))
        if (
            c.db
            or c.catalog
            or c.is_star
            or table is None
            or c.name not in table.columns
        ):
            return None
    # Exclude volatile functions and operators outside the simple row-filter domain.
    allowed = (exp.And, exp.Or, exp.Not)
    if any(isinstance(n, exp.Func) and not isinstance(n, allowed) for n in tree.walk()):
        return None
    owner = output[0].table
    table = schema.tables[aliases[owner.casefold()]]
    keys = sorted(
        n for n, c in table.columns.items() if getattr(c, "is_primary_key", False)
    )
    if not keys or any(
        str(table.columns[k].data_type).lower().split("(", 1)[0]
        not in {"int", "integer", "bigint", "smallint", "tinyint", "mediumint"}
        for k in keys
    ):
        return None
    missing = [
        k
        for k in keys
        if not any(
            c.table.casefold() == owner.casefold() and c.name == k for c in ranks
        )
    ]
    if not missing:
        return None
    candidate = tree.copy()
    candidate.args["order"].set(
        "expressions",
        candidate.args["order"].expressions
        + [
            exp.Ordered(
                this=exp.column(k, table=owner, quoted=True),
                desc=False,
                nulls_first=True,
            )
            for k in missing
        ],
    )
    identity = [exp.column(k, table=owner, quoted=True) for k in keys]
    before = tree.copy()
    after = candidate.copy()
    augmented = (
        [e.copy() for e in tree.expressions] + [c.copy() for c in ranks] + identity
    )
    before.set("expressions", [e.copy() for e in augmented])
    after.set("expressions", [e.copy() for e in augmented])
    # Prove uniqueness on the physical source rather than on duplicated joined rows.
    proof_alias = "__stable_identity"
    while proof_alias.casefold() in aliases:
        proof_alias += "_"
    source_name = aliases[owner.casefold()]
    key_matches = [
        exp.EQ(
            this=exp.column(k, table=proof_alias, quoted=True),
            expression=exp.column(k, table=owner, quoted=True),
        )
        for k in keys
    ]
    count = (
        exp.select(exp.Count(this=exp.Star()))
        .from_(
            exp.Table(
                this=exp.to_identifier(source_name, quoted=True),
                alias=exp.TableAlias(this=exp.to_identifier(proof_alias, quoted=True)),
            )
        )
        .where(exp.and_(*key_matches))
    )
    after.set("expressions", after.expressions + [exp.Subquery(this=count)])
    return StableFirstPlan(
        sql,
        candidate.sql(dialect=dialect),
        before.sql(dialect=dialect),
        after.sql(dialect=dialect),
        tuple(f"{owner}.{k}" for k in keys),
        len(output),
        len(ranks),
    )


def proven_stable_first_rows(plan, baseline, candidate):
    if len(baseline) != 1 or len(candidate) != 1:
        return None
    n, k, width = plan.output_columns, plan.rank_columns, len(plan.identity)
    before, after = baseline[0], candidate[0]
    if len(before) != n + k + width or len(after) != n + k + width + 1:
        return None
    if before[n : n + k] != after[n : n + k]:
        return None
    if type(after[-1]) is not int or after[-1] != 1:
        return None
    if any(type(v) is not int for v in after[n + k : -1]):
        return None
    return [tuple(after[:n])]


def accepts_stable_first_rows(expected, rows):
    return (
        expected is not None and len(rows) == 1 and [tuple(r) for r in rows] == expected
    )
