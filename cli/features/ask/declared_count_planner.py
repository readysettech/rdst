"""Resolve one declared-entity count edit. A supported plan never authorizes adoption."""
from __future__ import annotations

import hashlib

import sqlglot
from sqlglot import exp


VERSION = "declared-count-planner-v1"
INSERTION = "DISTINCT "


class Unsupported(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise Unsupported(reason)


def _only(node, allowed, reason):
    _need(all(k in allowed or not v for k, v in node.args.items()), reason)


def _bare(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _terms(node, operator):
    node = _bare(node)
    return _terms(node.this, operator) + _terms(node.expression, operator) if isinstance(node, operator) else [node]


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source(node, sql, dialect):
    positions = [n.meta for n in node.walk() if "start" in n.meta and "end" in n.meta]
    _need(bool(positions), "missing-source-position")
    start, end = min(m["start"] for m in positions), max(m["end"] for m in positions) + 1
    _need(0 <= start < end <= len(sql), "invalid-source-position")
    # sqlglot omits source metadata on operators, parentheses, NULL and Boolean.
    # Find the smallest bounded token span that reparses to this exact predicate.
    tokens = sqlglot.Dialect.get_or_raise(dialect).tokenize(sql)
    first = next(i for i, token in enumerate(tokens) if token.start <= start <= token.end)
    last = next(i for i, token in enumerate(tokens) if token.start <= end - 1 <= token.end)
    found = None
    for expansion in range(17):
        for before in range(expansion + 1):
            after = expansion - before
            left, right = first - before, last + after
            if left < 0 or right >= len(tokens):
                continue
            candidate_start, candidate_end = tokens[left].start, tokens[right].end + 1
            try:
                statement = sqlglot.parse_one("SELECT " + sql[candidate_start:candidate_end], read=dialect)
            except sqlglot.errors.ParseError:
                continue
            if (isinstance(statement, exp.Select) and len(statement.expressions) == 1
                    and all(k == "expressions" or not v for k, v in statement.args.items())
                    and _bare(statement.expressions[0]) == _bare(node)):
                found = candidate_start, candidate_end
                break
        if found:
            break
    _need(found is not None, "predicate-source-span-unresolved")
    start, end = found
    return dict(sql=node.sql(dialect=dialect), source_sql=sql[start:end],
                character_start=start, character_end=end,
                utf8_byte_start=len(sql[:start].encode("utf-8")),
                utf8_byte_end=len(sql[:end].encode("utf-8")))


def _table_metadata(tables, name):
    _need(name in tables and isinstance(tables[name], dict), "missing-table-metadata")
    table = tables[name]
    columns = table.get("columns")
    _need(isinstance(columns, (dict, list)) and bool(columns), "missing-column-metadata")
    columns = list(columns)
    _need(all(isinstance(c, str) and c for c in columns) and len(set(columns)) == len(columns), "invalid-column-metadata")
    pk = table.get("primary_key", [])
    _need(isinstance(pk, list) and all(isinstance(c, str) and c in columns for c in pk) and len(set(pk)) == len(pk), "invalid-primary-key-metadata")
    foreign = table.get("foreign_keys", [])
    _need(isinstance(foreign, list), "invalid-foreign-key-metadata")
    return dict(columns=columns, primary_key=pk, foreign_keys=foreign)


def _table(node, occurrence, tables):
    _need(isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier), "physical-table-required")
    _only(node, {"this", "alias"}, "qualified-or-modified-table")
    alias = node.args.get("alias")
    if alias is not None:
        _need(isinstance(alias.this, exp.Identifier), "invalid-table-alias")
        _only(alias, {"this"}, "table-alias-column-list")
    _table_metadata(tables, node.name)
    return dict(id=occurrence, table=node.name, alias=node.alias_or_name)


def _column(node, relations, tables):
    _need(isinstance(node, exp.Column) and isinstance(node.this, exp.Identifier)
          and isinstance(node.args.get("table"), exp.Identifier), "qualified-direct-column-required")
    _only(node, {"this", "table"}, "multipart-column-unsupported")
    matches = [r for r in relations if r["alias"] == node.table]
    _need(len(matches) == 1, "unresolved-or-correlated-column")
    relation = matches[0]
    _need(node.name in _table_metadata(tables, relation["table"])["columns"], "unknown-column")
    return relation, node.name


def _foreign_options(association, parent, tables):
    """IDs derive from all declared FK tuples, never from selected join branches."""
    metadata = _table_metadata(tables, association["table"])
    options, roles = [], []
    signatures = set()
    for fk in metadata["foreign_keys"]:
        _need(isinstance(fk, dict), "invalid-foreign-key-metadata")
        columns, target, references = fk.get("columns"), fk.get("referenced_table"), fk.get("referenced_columns")
        _need(isinstance(columns, list) and columns and isinstance(target, str)
              and isinstance(references, list) and len(columns) == len(references)
              and all(isinstance(c, str) and c in metadata["columns"] for c in columns)
              and all(isinstance(c, str) for c in references)
              and len(set(columns)) == len(columns), "invalid-foreign-key-metadata")
        target_meta = _table_metadata(tables, target)
        _need(all(c in target_meta["columns"] for c in references), "invalid-reference-column")
        signature = (tuple(columns), target, tuple(references))
        _need(signature not in signatures, "duplicate-declared-foreign-key")
        signatures.add(signature)
        complete = len(columns) == 1 and len(target_meta["primary_key"]) == 1 and references == target_meta["primary_key"]
        if target == parent["table"]:
            _need(complete, "non-scalar-or-incomplete-parent-role")
            roles.append((columns[0], references[0]))
        if complete:
            options.append((columns[0], target, references[0]))
    child_options = [dict(id=f"child{n}", table=table, primary_key=[key],
                          association_column=column, referenced_column=key,
                          association_relation_id=association["id"])
                     for n, (column, table, key) in enumerate(sorted(options))]
    parent_roles = [dict(id=f"parent_role{n}", association_column=column,
                        parent_column=key, parent_relation_id=parent["id"],
                        association_relation_id=association["id"])
                    for n, (column, key) in enumerate(sorted(roles))]
    return child_options, parent_roles


def _literal(node):
    node = _bare(node)
    return isinstance(node, (exp.Literal, exp.Null, exp.Boolean)) or (
        isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal) and not node.this.is_string)


def _predicate(term, relations, tables, parent, association, sql, dialect, index):
    term = _bare(term)
    _need(isinstance(term, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is, exp.In)), "unsupported-local-predicate")
    if isinstance(term, exp.In):
        _only(term, {"this", "expressions"}, "nonliteral-in-predicate")
        _need(0 < len(term.expressions) <= 32 and all(_literal(n) for n in term.expressions), "nonliteral-in-predicate")
        column, values = _bare(term.this), term.expressions
    else:
        _only(term, {"this", "expression"}, "modified-comparison")
        left, right = _bare(term.this), _bare(term.expression)
        if isinstance(left, exp.Column) and _literal(right):
            column, values = left, [right]
        elif not isinstance(term, exp.Is) and _literal(left) and isinstance(right, exp.Column):
            column, values = right, [left]
        else:
            raise Unsupported("comparison-must-have-one-column-and-literal")
        if isinstance(term, exp.Is):
            _need(isinstance(right, (exp.Null, exp.Boolean)), "unsupported-is-predicate")
    relation, name = _column(column, relations, tables)
    owner = "parent" if relation["id"] == parent["id"] else "association"
    _need(relation["id"] in {parent["id"], association["id"]}, "predicate-ownership-unknown")
    return dict(id=f"predicate{index}", owner=owner, relation_id=relation["id"],
                column=name, operator=term.key, literal_sql=[v.sql(dialect=dialect) for v in values],
                **_source(term, sql, dialect))


def _plan(sql, schema, dialect):
    _need(isinstance(sql, str) and 0 < len(sql) <= 32768, "invalid-or-oversized-sql")
    _need(dialect in {"mysql", "postgres", "postgresql"}, "unsupported-dialect")
    dialect = "postgres" if dialect == "postgresql" else dialect
    _need(isinstance(schema, dict) and isinstance(schema.get("tables"), dict), "invalid-schema")
    tables = schema["tables"]
    tokens = sqlglot.Dialect.get_or_raise(dialect).tokenize(sql)
    _need(not any(t.token_type.name == "HINT" or any(c.lstrip().casefold().startswith(("!", "+", "m!")) for c in t.comments) for t in tokens), "executable-comment-or-hint")
    terminators = [(i, t) for i, t in enumerate(tokens) if t.token_type.name == "SEMICOLON"]
    _need(len(terminators) <= 1 and (not terminators or terminators[0][0] == len(tokens) - 1), "extra-statement-or-terminator")
    if terminators:
        suffix = sql[terminators[0][1].end + 1:]
        # MySQL's tokenizer strips '+' from a hint following a semicolon. Keep
        # the existing conservative hint veto even at this non-query position.
        _need(not any(marker in suffix.casefold() for marker in ("/*+", "/*!", "/*m!")), "executable-comment-or-hint")
    # A comment attached to the final semicolon becomes a separate sqlglot
    # Semicolon expression. Token validation proves the suffix has no other SQL.
    # Parse the statement prefix, preserving the full original bytes for the edit.
    statement_sql = sql[:terminators[0][1].start] if terminators else sql
    statements = sqlglot.parse(statement_sql, read=dialect)
    _need(len(statements) == 1 and isinstance(statements[0], exp.Select), "exactly-one-select-statement-required")
    outer = statements[0]
    _only(outer, {"expressions", "from_"}, "unsupported-outer-operation")
    _need(len(outer.expressions) == 1 and len(list(outer.find_all(exp.Select))) == 2, "exact-two-scope-shape-required")
    source = outer.args.get("from_")
    _need(source is not None, "missing-derived-source")
    _only(source, {"this"}, "extra-derived-source")
    derived = source.this
    _need(isinstance(derived, exp.Subquery) and isinstance(derived.this, exp.Select), "single-derived-source-required")
    _only(derived, {"this", "alias"}, "modified-derived-source")
    alias = derived.args.get("alias")
    _need(alias is not None and isinstance(alias.this, exp.Identifier), "derived-alias-required")
    _only(alias, {"this"}, "derived-alias-column-list")
    outer_output = outer.expressions[0]
    if isinstance(outer_output, exp.Alias):
        _need(isinstance(outer_output.args.get("alias"), exp.Identifier), "invalid-output-alias")
        _only(outer_output, {"this", "alias"}, "modified-output-alias")
        outer_output = outer_output.this
    outer_output = _bare(outer_output)
    _need(isinstance(outer_output, exp.Avg), "bare-unweighted-avg-required")
    _only(outer_output, {"this"}, "modified-avg")
    avg_column = outer_output.this
    _need(isinstance(avg_column, exp.Column) and isinstance(avg_column.this, exp.Identifier)
          and avg_column.table in {"", derived.alias}, "qualified-derived-count-reference-required")
    _only(avg_column, {"this", "table"}, "multipart-avg-reference")
    inner = derived.this
    _only(inner, {"expressions", "from_", "joins", "where", "group"}, "unsupported-inner-operation")
    _need(len(inner.expressions) == 2, "parent-key-and-count-only")
    count_items = [n for n in inner.expressions if isinstance(n, exp.Alias) and isinstance(n.this, exp.Count)]
    _need(len(count_items) == 1, "one-aliased-bare-count-required")
    count_item = count_items[0]
    _only(count_item, {"this", "alias"}, "modified-count-alias")
    _need(isinstance(count_item.args.get("alias"), exp.Identifier) and count_item.alias == avg_column.name, "count-alias-mismatch")
    count = count_item.this
    _only(count, {"this", "big_int"}, "modified-count")
    _need(isinstance(count.this, exp.Column), "bare-column-count-required")
    parent_item = next(n for n in inner.expressions if n is not count_item)
    parent_column = parent_item.this if isinstance(parent_item, exp.Alias) else parent_item
    if isinstance(parent_item, exp.Alias):
        _only(parent_item, {"this", "alias"}, "modified-parent-alias")
        _need(isinstance(parent_item.args.get("alias"), exp.Identifier), "invalid-parent-output-alias")
    _need(parent_item.alias_or_name != count_item.alias, "duplicate-inner-output-name")
    if not avg_column.table:
        # The sole outer source was already proved to be this derived table.
        # Resolve only its unique output name; no SQL qualification is inserted.
        # Case-folding here is conservative for quoted PostgreSQL identifiers.
        matches = [item for item in inner.expressions
                   if item.alias_or_name.casefold() == avg_column.name.casefold()]
        _need(len(matches) == 1 and matches[0] is count_item,
              "ambiguous-unqualified-derived-count-output")
    inner_from = inner.args.get("from_")
    _need(inner_from is not None, "missing-inner-source")
    _only(inner_from, {"this"}, "extra-inner-source")
    joins = inner.args.get("joins", [])
    _need(len(joins) == 1, "exactly-one-inner-join-required")
    join = joins[0]
    _only(join, {"this", "on", "kind"}, "unsupported-join-modifier")
    _need(join.kind in {"", "INNER"} and join.args.get("on") is not None, "inner-on-join-required")
    relations = [_table(inner_from.this, "s1.r0", tables), _table(join.this, "s1.r1", tables)]
    _need(relations[0]["alias"] != relations[1]["alias"], "duplicate-inner-alias")
    parent, parent_key = _column(parent_column, relations, tables)
    association, child_column = _column(count.this, relations, tables)
    _need(parent["id"] != association["id"], "parent-and-association-must-differ")
    _need(parent["table"] != association["table"], "parent-association-self-join-unsupported")
    parent_pk = _table_metadata(tables, parent["table"])["primary_key"]
    _need(parent_pk == [parent_key], "complete-scalar-parent-key-required")
    parent = dict(parent, primary_key=parent_pk)
    group = inner.args.get("group")
    _need(isinstance(group, exp.Group) and len(group.expressions) == 1, "full-parent-key-group-required")
    _only(group, {"expressions"}, "modified-grouping")
    group_relation, group_key = _column(group.expressions[0], relations, tables)
    _need(group_relation["id"] == parent["id"] and group_key == parent_key, "group-must-equal-full-parent-key")
    # All columns are resolved in their own scope. No implicit or outer reference survives.
    for column in inner.find_all(exp.Column):
        _column(column, relations, tables)
    child_options, parent_roles = _foreign_options(association, parent, tables)
    child_matches = [c for c in child_options if c["association_column"] == child_column]
    _need(len(child_matches) == 1, "count-argument-needs-one-complete-scalar-child-key")
    _need(parent_roles, "declared-parent-role-required")
    branches = _terms(join.args["on"], exp.Or)
    _need(1 <= len(branches) <= 2, "one-or-two-parent-role-branches-required")
    join_branches, present = [], []
    for index, branch in enumerate(branches):
        branch = _bare(branch)
        _need(isinstance(branch, exp.EQ), "parent-join-branch-must-be-equality")
        _only(branch, {"this", "expression"}, "modified-join-equality")
        left, left_column = _column(_bare(branch.this), relations, tables)
        right, right_column = _column(_bare(branch.expression), relations, tables)
        if left["id"] == association["id"]:
            left, right, left_column, right_column = right, left, right_column, left_column
        _need(left["id"] == parent["id"] and right["id"] == association["id"] and left_column == parent_key, "join-roles-or-parent-key-mismatch")
        matches = [r for r in parent_roles if r["association_column"] == right_column and r["parent_column"] == left_column]
        _need(len(matches) == 1, "join-branch-needs-declared-parent-role")
        role = matches[0]["id"]
        _need(role not in present, "duplicate-parent-role-branch")
        present.append(role)
        join_branches.append(dict(id=f"join{index}", parent_role_id=role, **_source(branch, sql, dialect)))
    where = inner.args.get("where")
    if where is not None:
        _only(where, {"this"}, "modified-where")
    predicates = [_predicate(term, relations, tables, parent, association, sql, dialect, index)
                  for index, term in enumerate(_terms(where.this, exp.And) if where else [])]
    _need(len(predicates) <= 16, "too-many-local-predicates")
    offset = count.this.args["table"].meta.get("start")
    _need(type(offset) is int, "missing-count-argument-position")
    candidate = sql[:offset] + INSERTION + sql[offset:]
    byte_offset = len(sql[:offset].encode("utf-8"))
    _need(candidate[:offset] + candidate[offset + len(INSERTION):] == sql, "character-edit-conservation-failed")
    raw = candidate.encode("utf-8")
    _need(raw[:byte_offset] + raw[byte_offset + len(INSERTION):] == sql.encode("utf-8"), "byte-edit-conservation-failed")
    candidate_statement = candidate[:len(statement_sql) + len(INSERTION)] if terminators else candidate
    reparsed = sqlglot.parse_one(candidate_statement, read=dialect)
    new_counts = list(reparsed.find_all(exp.Count))
    _need(len(new_counts) == 1 and isinstance(new_counts[0].this, exp.Distinct)
          and len(new_counts[0].this.expressions) == 1, "candidate-must-add-one-count-distinct")
    new_counts[0].set("this", new_counts[0].this.expressions[0].copy())
    _need(reparsed == outer, "candidate-ast-conservation-failed")
    child = child_matches[0]
    catalog = dict(version=VERSION, parent=parent, association=association, child=child,
                   child_options=child_options, counted_child_option_id=child["id"],
                   parent_roles=parent_roles, present_parent_role_ids=sorted(present),
                   join_branches=join_branches, predicates=predicates,
                   parent_predicate_ids=[p["id"] for p in predicates if p["owner"] == "parent"],
                   association_predicate_ids=[p["id"] for p in predicates if p["owner"] == "association"],
                   count_alias=count_item.alias, derived_alias=derived.alias)
    return dict(supported=True, semantic_acceptance=False,
                reason="structurally-eligible-semantic-and-native-proof-required", dialect=dialect,
                original_sql=sql, candidate_sql=candidate, original_sha256=_hash(sql),
                candidate_sha256=_hash(candidate), insertion=dict(text=INSERTION,
                character_offset=offset, utf8_byte_offset=byte_offset), catalog=catalog)


def plan_count(sql: str, schema: dict, dialect: str = "mysql") -> dict:
    """Return an exact one-insertion proposal or a conservative structural rejection.

    The caller must separately bind the complete question's counted identity,
    role set and predicates and obtain qualified native proof. This module has
    no model, database, filesystem or application runtime dependency.
    """
    try:
        return _plan(sql, schema, dialect)
    except Unsupported as error:
        return dict(supported=False, reason=str(error))
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, UnicodeError):
        return dict(supported=False, reason="sql-parse-or-encoding-error")
