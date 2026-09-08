"""Construct one combined count proof and candidate statement.

Construction grants no execution permission. The native adapter owns one fresh bounded call.
"""
from hashlib import sha256

import sqlglot
from sqlglot import exp

from .declared_count_planner import plan_count
from .declared_count_proof import COUNTERS, ROLE_COUNTERS, SCALARS


def digest(sql):
    return sha256(sql.encode("utf-8")).hexdigest()


def _without_terminator(sql, dialect):
    tokens = sqlglot.Dialect.get_or_raise(dialect).tokenize(sql)
    semis = [t for t in tokens if t.token_type.name == "SEMICOLON"]
    if not semis:
        return sql, None
    if len(semis) != 1 or semis[0] is not tokens[-1]:
        raise ValueError("Only a final statement terminator may be removed")
    token = semis[0]
    if sql[token.start:token.end + 1] != ";":
        raise ValueError("Invalid final terminator position")
    return sql[:token.start] + sql[token.end + 1:], dict(
        character_offset=token.start, utf8_byte_offset=len(sql[:token.start].encode()), text=";")


def build_wrapper(sql, schema, dialect="mysql"):
    plan = plan_count(sql, schema, dialect)
    if not plan["supported"]:
        return dict(supported=False, reason=plan["reason"])
    dialect = plan["dialect"]
    catalog = plan["catalog"]
    original_body, original_terminator = _without_terminator(sql, dialect)
    candidate_body, candidate_terminator = _without_terminator(plan["candidate_sql"], dialect)
    parsed = sqlglot.parse_one(original_body, read=dialect)
    inner = parsed.args["from_"].this.this
    identifiers = {n.name.casefold() for n in parsed.find_all(exp.Identifier)}
    # The physical child may occur only in metadata. A proof CTE must never
    # shadow that source, even though it was absent from the candidate SQL.
    identifiers.update(name.casefold() for name in schema["tables"])
    prefix_index = 0
    while any(name.startswith(f"__e177_{prefix_index}_") for name in identifiers):
        prefix_index += 1
    prefix = f"__e177_{prefix_index}_"
    names = {name: prefix + name for name in (
        "selected", "incidence", "checked", "raw_groups", "fk_pairs", "physical_pairs",
        "physical_groups", "groups", "v", "c", "r", "g", "f", "candidate", "old")}
    original_relations = [inner.args["from_"].this, inner.args["joins"][0].this]
    original_aliases = {table.alias_or_name: (table.args["alias"].this if table.args.get("alias") else table.this)
                        for table in original_relations}

    def ident(name):
        return exp.to_identifier(name, quoted=True).sql(dialect=dialect)

    def col(alias, name):
        # PostgreSQL folds unquoted P to p. Quoting it as "P" would change
        # resolution, so preserve the source alias's original Identifier.
        owner = original_aliases[alias].sql(dialect=dialect) if alias in original_aliases else ident(alias)
        return owner + "." + ident(name)

    def cte(name):
        return ident(names[name])

    def field(name):
        return ident(name)

    parent, association, child = (catalog[k] for k in ("parent", "association", "child"))
    pk = parent["primary_key"][0]
    parent_sql = next(table.sql(dialect=dialect) for table in original_relations if table.alias_or_name == parent["alias"])
    pk_expr = inner.args["group"].expressions[0].sql(dialect=dialect)
    fk_expr = next(node.this for node in inner.expressions if isinstance(node, exp.Alias) and isinstance(node.this, exp.Count)).this.sql(dialect=dialect)
    parent_filters = [p["sql"] for p in catalog["predicates"] if p["owner"] == "parent"]
    parent_where = " WHERE " + " AND ".join("(" + p + ")" for p in parent_filters) if parent_filters else ""
    # Proof relations reuse the entire original FROM/ON/WHERE, with no narrowing.
    inner_source = inner.args["from_"].sql(dialect=dialect)
    inner_source += " " + " ".join(j.sql(dialect=dialect) for j in inner.args["joins"])
    if inner.args.get("where") is not None:
        inner_source += " " + inner.args["where"].sql(dialect=dialect)
    roles = catalog["parent_roles"]
    endpoint_aliases = {r["id"]: "endpoint" + str(i) for i, r in enumerate(roles)}
    incidence_fields = [pk_expr + " AS " + field("pk"), fk_expr + " AS " + field("fk")]
    for role in roles:
        incidence_fields.append(col(association["alias"], role["association_column"]) + " AS " + field(endpoint_aliases[role["id"]]))
    v, c, r = (names[k] for k in ("v", "c", "r"))
    child_table = ident(child["table"])
    child_key = child["primary_key"][0]
    child_join = col(c, child_key) + " = " + col(v, "fk")
    checked_fields = [ident(v) + ".*", "(SELECT COUNT(*) FROM " + child_table + " " + ident(c) + " WHERE " + child_join + ") AS " + field("child_matches")]
    for role in roles:
        endpoint = endpoint_aliases[role["id"]]
        checked_fields.append("(SELECT COUNT(*) FROM " + ident(parent["table"]) + " " + ident(r) +
            " WHERE " + col(r, pk) + " = " + col(v, endpoint) + ") AS " + field(endpoint + "_matches"))
    definitions = [
        ("selected", "SELECT " + pk_expr + " AS " + field("pk") + " FROM " + parent_sql + parent_where),
        ("incidence", "SELECT " + ", ".join(incidence_fields) + " " + inner_source),
        ("checked", "SELECT " + ", ".join(checked_fields) + " FROM " + cte("incidence") + " " + ident(v)),
        ("raw_groups", "SELECT " + field("pk") + ", COUNT(" + field("fk") + ") AS " + field("raw_n") +
            ", COUNT(DISTINCT " + field("fk") + ") AS " + field("fk_n") + " FROM " + cte("incidence") + " GROUP BY " + field("pk")),
        ("fk_pairs", "SELECT DISTINCT " + field("pk") + ", " + field("fk") + " FROM " + cte("incidence") + " WHERE " + field("fk") + " IS NOT NULL"),
        ("physical_pairs", "SELECT DISTINCT " + col(v, "pk") + " AS " + field("pk") + ", " + col(c, child_key) +
            " AS " + field("physical_key") + " FROM " + cte("incidence") + " " + ident(v) + " JOIN " + child_table + " " + ident(c) + " ON " + child_join),
        ("physical_groups", "SELECT " + field("pk") + ", COUNT(*) AS " + field("physical_n") + " FROM " + cte("physical_pairs") + " GROUP BY " + field("pk")),
        ("groups", "SELECT " + col(names["g"], "pk") + " AS " + field("pk") + ", " + col(names["g"], "raw_n") +
            " AS " + field("raw_n") + ", " + col(names["g"], "fk_n") + " AS " + field("fk_n") + ", " + col(names["f"], "physical_n") +
            " AS " + field("physical_n") + " FROM " + cte("raw_groups") + " " + ident(names["g"]) + " LEFT JOIN " + cte("physical_groups") +
            " " + ident(names["f"]) + " ON " + col(names["g"], "pk") + " = " + col(names["f"], "pk")),
    ]

    def count(name, where=None, operand="*"):
        return "(SELECT COUNT(" + operand + ") FROM " + cte(name) + (" WHERE " + where if where else "") + ")"

    def absent(left, right):
        return ("(SELECT COUNT(*) FROM " + cte(left) + " " + ident(v) + " WHERE NOT EXISTS (SELECT 1 FROM " + cte(right) +
            " " + ident(r) + " WHERE " + col(r, "pk") + " = " + col(v, "pk") + "))")

    values = {
        "selected_parent_count": count("selected"),
        "grouped_parent_count": count("raw_groups"),
        "unique_grouped_parent_count": count("raw_groups", operand="DISTINCT " + field("pk")),
        "omitted_selected_parents": absent("selected", "raw_groups"),
        "unexpected_grouped_parents": absent("raw_groups", "selected"),
        "duplicate_parent_groups": count("raw_groups") + " - " + count("raw_groups", operand="DISTINCT " + field("pk")),
        "selected_incidence_rows": count("incidence"),
        "selected_null_child_keys": count("checked", field("fk") + " IS NULL"),
        "nonnull_child_orphans": count("checked", field("fk") + " IS NOT NULL AND " + field("child_matches") + " = 0"),
        "ambiguous_child_reference_rows": count("checked", field("child_matches") + " > 1"),
        "matched_child_reference_rows": count("checked", field("child_matches") + " = 1"),
        "raw_count_total": count("incidence", operand=field("fk")),
        "distinct_parent_child_total": count("fk_pairs"),
        "matched_parent_child_total": count("physical_pairs"),
        "per_parent_key_comparison_mismatches": count("groups", field("physical_n") + " IS NULL OR " + field("fk_n") + " <> " + field("physical_n")),
        "per_parent_count_inconsistencies": count("groups", field("raw_n") + " < " + field("fk_n") + " OR " + field("fk_n") + " < 1 OR " + field("physical_n") + " IS NULL"),
        "duplicate_reference_difference": count("incidence", operand=field("fk")) + " - " + count("fk_pairs"),
    }
    assert set(values) == set(COUNTERS)
    values["raw_native_average"] = "(\n" + original_body + "\n)"
    values["distinct_native_average"] = "(SELECT AVG(" + field("physical_n") + ") FROM " + cte("physical_groups") + ")"
    role_columns = {}
    for i, role in enumerate(roles):
        endpoint = endpoint_aliases[role["id"]]
        formulas = dict(reference_rows=count("incidence"),
            null_keys=count("checked", field(endpoint) + " IS NULL"),
            orphan_keys=count("checked", field(endpoint) + " IS NOT NULL AND " + field(endpoint + "_matches") + " = 0"),
            ambiguous_matches=count("checked", field(endpoint + "_matches") + " > 1"),
            matched_rows=count("checked", field(endpoint + "_matches") + " = 1"))
        role_columns[role["id"]] = {}
        for key in ROLE_COUNTERS:
            name = f"role{i}_{key}"
            role_columns[role["id"]][key] = name
            values[name] = formulas[key]
    columns = list(values) + ["candidate_native_scalar"]
    prefix_sql = "WITH\n" + ",\n".join(cte(name) + " AS (" + body + ")" for name, body in definitions)
    prefix_sql += "\nSELECT\n" + ",\n".join(value + " AS " + ident(name) for name, value in values.items())
    prefix_sql += ",\n(\n"
    wrapper = prefix_sql + candidate_body + "\n) AS " + ident("candidate_native_scalar")
    offset = len(prefix_sql)
    tree = sqlglot.parse_one(wrapper, read=dialect)
    candidate_subquery = tree.expressions[-1].this
    if not isinstance(candidate_subquery, exp.Subquery) or candidate_subquery.this != sqlglot.parse_one(candidate_body, read=dialect):
        raise ValueError("Embedded candidate AST changed")
    if wrapper[offset:offset + len(candidate_body)] != candidate_body:
        raise ValueError("Embedded candidate bytes changed")
    if [item.alias for item in tree.expressions] != columns:
        raise ValueError("Proof output columns changed")
    return dict(supported=True, executable=False,
        reason="combined-count-statement-requires-fresh-bounded-execution",
        plan=plan, wrapper_sql=wrapper, wrapper_sha256=digest(wrapper), columns=columns,
        role_columns=role_columns, scalar_columns=list(SCALARS),
        original_terminator=original_terminator, candidate_terminator=candidate_terminator,
        candidate_embedding=dict(character_start=offset, character_end=offset + len(candidate_body),
            utf8_byte_start=len(prefix_sql.encode()), utf8_byte_end=len((prefix_sql + candidate_body).encode()),
            sha256=digest(candidate_body)),
        candidate_subtree_equal=True, logical_candidate_invocations=1,
        data_statements=1, new_native_queries=0)


def decode_row(wrapper, columns, rows):
    """Map a complete native row; never coerce values or grant provenance."""
    if columns != wrapper["columns"] or type(rows) is not list or len(rows) != 1:
        raise ValueError("Exact single native row and ordered columns required")
    if type(rows[0]) not in (list, tuple) or len(rows[0]) != len(columns):
        raise ValueError("Incomplete native row")
    flat = dict(zip(columns, rows[0]))
    result = {key: flat[key] for key in COUNTERS + SCALARS}
    result["endpoint_roles"] = {role: {key: flat[name] for key, name in aliases.items()}
        for role, aliases in wrapper["role_columns"].items()}
    return result
