"""Which tables and columns a query reads, resolved through aliases, CTEs and stars.

Restrictions compare what the database will read, not the spelling of the query:
``SELECT c.ssn FROM customers c`` reads ``customers.ssn`` and ``SELECT *`` reads every
column of its tables. Anything that cannot be resolved counts against the query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Iterable, Union

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.pushdown_projections import pushdown_projections
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope, walk_in_scope
from sqlglot.schema import MappingSchema

_READ_DIALECTS = {"postgresql": "postgres", "postgres": "postgres", "mysql": "mysql"}

# In every refusal that says the query could not be verified rather than that it breaks a
# restriction, so a caller can tell the two apart.
UNVERIFIED = "could not be checked"

# Ends every refusal of a whole-row read whose table's columns are unknown, so a caller can
# say how to supply them.
UNKNOWN_COLUMNS = f"{UNVERIFIED} against denied columns"


def unverified(message: str) -> bool:
    """Whether a refusal says the query could not be verified, not that it breaks a rule."""
    return UNVERIFIED in message


@dataclass(frozen=True)
class SchemaInfo:
    """What the target says about table names: the schemas an unqualified name searches,
    in order, and the columns of the tables in them. ``exact`` means an unqualified name
    is in the first schema whether or not it is listed (MySQL's connected database)."""

    search_path: tuple[str, ...]
    columns: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    exact: bool = False

    def schema_of(self, table: str) -> str | None:
        """The schema an unqualified ``table`` resolves to, or None when it is unknown."""
        for schema in self.search_path:
            if (schema, table) in self.columns:
                return schema
        return self.search_path[0] if self.exact and self.search_path else None


# Either a target's SchemaInfo or, for one schema, a map of its tables to their columns.
Tables = Union[SchemaInfo, dict[str, Iterable[str]], None]


def default_schema_for(target_config: dict[str, Any] | None) -> str | None:
    """The schema an unqualified table name resolves to on the target, when the config
    alone decides it: MySQL's connected database. PostgreSQL searches its search_path,
    which only the server knows (see ``SchemaInfo``)."""
    config = target_config or {}
    if str(config.get("engine") or "").lower() == "mysql":
        return str(config.get("database") or "").lower() or None
    return None


def _catalog(tables: Tables, default_schema: str | None) -> SchemaInfo:
    if isinstance(tables, SchemaInfo):
        return tables
    schema = (default_schema or "").lower()
    columns = {(schema, name.lower()): [col.lower() for col in cols]
               for name, cols in (tables or {}).items()}
    # A table map belongs to the schema unqualified names resolve to, named or not.
    return SchemaInfo((schema,), columns, exact=bool(schema) or bool(tables))


def denied_column_violation(
    sql: str,
    denied_columns: Iterable[str],
    *,
    dialect: str = "",
    tables: Tables = None,
    default_schema: str | None = None,
) -> str | None:
    """A message naming a denied column ``sql`` reads, or None when it reads none.

    ``tables`` (a target's ``SchemaInfo``, or the default schema's tables and their
    columns) resolves stars and unqualified columns to real columns. Without it, a star or
    whole-row reference is refused and an unresolved column is matched on its name alone.
    """
    patterns = [pattern.lower() for pattern in denied_columns]
    if not patterns:
        return None
    known = _catalog(tables, default_schema)
    try:
        statement = _resolved(_parse(sql, dialect), known)
        # Row values are found on the statement as written: qualification may rewrite them
        # into ordinary-looking columns.
        rows = _row_references(statement, dialect, known)
        if isinstance(rows, str):
            return rows
        for source in rows:
            if isinstance(source, Scope):
                # Checked as its own scope, unpruned.
                continue
            message = _whole_row_violation(source, patterns, known)
            if message:
                return message
        # A row value of a subquery reads every column it selects, so none may be pruned.
        tree = _qualified(statement, dialect, known, prune=not rows)
        for scope in traverse_scope(tree):
            for column in scope.columns:
                source = _source(scope, column.table)
                if isinstance(source, Scope):
                    # A derived table's own scope checks what it reads.
                    continue
                name = column.name.lower()
                if isinstance(source, exp.Table):
                    candidates = _column_names(source, name)
                    if any(fnmatch(c, p) for c in candidates for p in patterns):
                        return f"Access to column '{candidates[1]}' is denied"
                elif any(fnmatch(name, p.rsplit(".", 1)[-1]) for p in patterns):
                    return f"Access to column '{name}' is denied"
            for source in _whole_row_sources(scope):
                if isinstance(source, Scope):
                    continue
                message = _whole_row_violation(source, patterns, known)
                if message:
                    return message
    except Exception as exc:
        return f"The query {UNVERIFIED} against denied columns: {exc}"
    return None


def disallowed_table_violation(
    sql: str,
    allowed_tables: Iterable[str],
    *,
    dialect: str = "",
    default_schema: str | None = None,
    tables: Tables = None,
) -> str | None:
    """A message naming a table ``sql`` reads outside ``allowed_tables``, or None.

    An entry without a schema allows that table in any schema; ``schema.table`` allows only
    that schema's table. A table the query names without a schema is in the schema the
    target resolves it to (``tables``, or ``default_schema``); when that is unknown it
    cannot be checked against a schema-qualified entry. A table function in FROM is not a
    table and is refused.
    """
    known = _catalog(tables, default_schema)
    read = _READ_DIALECTS.get((dialect or "").lower())
    anywhere, qualified = set(), set()
    for entry in allowed_tables:
        try:
            table = exp.to_table(entry, dialect=read)
            schema, name = _folded(table.args.get("db"), read), _folded(table.this, read)
        except Exception:
            parts = entry.lower().split(".")
            schema, name = (parts[-2] if len(parts) > 1 else None), parts[-1]
        if schema:
            qualified.add((schema, name))
        else:
            anywhere.add(name)
    if not anywhere and not qualified:
        return None
    try:
        tree = _qualified(_resolved(_parse(sql, dialect), known), dialect, SchemaInfo(()),
                          columns=False)
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if not isinstance(source, exp.Table):
                    continue
                if not isinstance(source.this, exp.Identifier):
                    return f"Table function '{source.this.sql()}' is not allowed"
                schema, name = _folded(source.args.get("db"), read), _folded(source.this, read)
                if name in anywhere or (schema, name) in qualified:
                    continue
                if schema is None and any(name == table for _, table in qualified):
                    return (f"Table '{name}' {UNVERIFIED} against allowed tables: the schema "
                            "it resolves to is unknown")
                shown = f"{schema}.{name}" if schema else name
                return f"Access to table '{shown}' is not allowed"
    except Exception as exc:
        return f"The query {UNVERIFIED} against allowed tables: {exc}"
    return None


def _folded(identifier: Any, read: str | None) -> str | None:
    """A table or schema name as the server tells names apart: PostgreSQL keeps a quoted
    name's case, so "Title_Basics" and title_basics are two tables."""
    if not isinstance(identifier, exp.Identifier) or not identifier.name:
        return None
    if read == "postgres" and identifier.quoted:
        return identifier.name
    return identifier.name.lower()


def _parse(sql: str, dialect: str) -> exp.Expression:
    read = _READ_DIALECTS.get((dialect or "").lower())
    statements = [statement for statement in sqlglot.parse(sql, read=read) if statement]
    if len(statements) != 1:
        raise ValueError("expected a single statement")
    return statements[0]


def _resolved(statement: exp.Expression, catalog: SchemaInfo) -> exp.Expression:
    """A copy of ``statement`` whose unqualified tables name the schema they resolve to.

    Each name is looked up along the search path on its own, as the server does: a
    table the first schema lacks comes from the next one that has it.
    """
    statement = statement.copy()
    for scope in traverse_scope(statement):
        for source in scope.sources.values():
            if (isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier)
                    and not source.db):
                schema = catalog.schema_of(source.name.lower())
                if schema:
                    source.set("db", exp.to_identifier(schema))
    return statement


def _qualified(
    statement: exp.Expression,
    dialect: str,
    catalog: SchemaInfo,
    columns: bool = True,
    prune: bool = True,
) -> exp.Expression:
    read = _READ_DIALECTS.get((dialect or "").lower())
    mapping: dict[str, dict[str, dict[str, str]]] = {}
    for (schema, table), cols in catalog.columns.items():
        mapping.setdefault(schema, {})[table] = {column: "text" for column in cols}
    # Tables of an unnamed schema are the ones unqualified names reach.
    flat = mapping.pop("", None)
    schema = (MappingSchema(flat, dialect=read) if flat and not mapping
              else MappingSchema(mapping, dialect=read) if mapping else None)
    options = dict(
        db=None, dialect=read, schema=schema,
        validate_qualify_columns=False, quote_identifiers=False, identify=False,
    )
    if not columns:
        return qualify(statement.copy(), qualify_columns=False, expand_stars=False, **options)
    try:
        tree = qualify(statement.copy(), **options)
    except OptimizeError:
        # Columns that cannot be placed without the schema stay unqualified and are
        # matched on their name alone.
        return qualify(statement.copy(), qualify_columns=False, **options)
    if not prune:
        return tree
    try:
        # Columns a subquery or CTE selects but nothing outside it uses are not read.
        return pushdown_projections(tree)
    except OptimizeError:
        # A star left unexpanded blocks pruning; every selected column then counts.
        return tree


def _source(scope: Scope | None, alias: str) -> Any:
    while scope is not None:
        for name, source in scope.sources.items():
            if name == alias or name.lower() == alias.lower():
                return source
        scope = scope.parent
    return None


def _source_columns(source: Any, catalog: SchemaInfo) -> list[str] | None:
    """Columns a source provides, or None when they are not known."""
    if isinstance(source, Scope):
        if any(isinstance(select, exp.Star) or (isinstance(select, exp.Column)
               and isinstance(select.this, exp.Star)) for select in source.expression.selects):
            return None
        return [name.lower() for name in source.expression.named_selects]
    if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
        return catalog.columns.get(((source.db or "").lower(), source.name.lower()))
    return None


def _row_references(
    statement: exp.Expression,
    dialect: str,
    catalog: SchemaInfo,
) -> list[Any] | str:
    """Sources the statement reads as whole rows, or a message when a name resolves to
    nothing it reads.

    PostgreSQL resolves a bare name to a column first and to a FROM item's row only when no
    column has it; ``t.*`` and ``schema.table`` name rows too. MySQL has no row values.
    """
    mysql = _READ_DIALECTS.get((dialect or "").lower()) == "mysql"
    rows: list[Any] = []
    for scope in traverse_scope(statement):
        chain = []
        level: Scope | None = scope
        while level is not None:
            chain.append(level)
            level = level.parent
        for node in walk_in_scope(scope.expression):
            if not isinstance(node, (exp.Column, exp.TableColumn)):
                continue
            name = node.name.lower()
            table = node.text("table").lower() if isinstance(node, exp.Column) else ""
            if isinstance(node, exp.TableColumn):
                rows.append(_source(scope, node.name))
            elif isinstance(node.this, exp.Star):
                rows.append(_source(scope, node.text("table")))
            elif table:
                if _source(scope, node.text("table")) is not None or mysql:
                    continue
                # schema.table names a FROM item's row in PostgreSQL.
                named = [source for level in chain for source in level.sources.values()
                         if isinstance(source, exp.Table) and source.name.lower() == name
                         and (source.db or "").lower() == table]
                if not named:
                    return f"'{node.sql()}' names nothing the query reads, so it {UNVERIFIED}"
                rows.extend(named)
            elif not mysql:
                source = next((level.sources[alias] for level in chain
                               for alias in level.sources if alias.lower() == name), None)
                if source is None:
                    continue
                provided = [_source_columns(other, catalog)
                            for level in chain for other in level.sources.values()]
                if any(cols is not None and name in cols for cols in provided):
                    continue
                rows.append(source)
    return rows


def _column_names(table: exp.Table, column: str) -> list[str]:
    name = table.name.lower()
    schema = (table.db or "").lower()
    names = [column, f"{name}.{column}"]
    return names + [f"{schema}.{name}.{column}"] if schema else names


def _whole_row_sources(scope: Scope) -> list[Any]:
    """Sources this scope reads whole: ``*`` left unexpanded, ``t.*`` and row values."""
    sources = []
    for node in walk_in_scope(scope.expression):
        if isinstance(node, exp.TableColumn):
            sources.append(_source(scope, node.name))
        elif isinstance(node, exp.Column) and isinstance(node.this, exp.Star):
            sources.append(_source(scope, node.table))
        elif isinstance(node, exp.Column) and not node.table and node.name in scope.sources:
            # Without the schema a row value reads as a column named after its table.
            sources.append(scope.sources[node.name])
        elif isinstance(node, exp.Star) and isinstance(node.parent, exp.Select):
            sources.extend(scope.sources.values())
    return sources


def _whole_row_violation(source: Any, patterns: list[str], catalog: SchemaInfo) -> str | None:
    if not isinstance(source, exp.Table):
        return f"A whole-row read {UNKNOWN_COLUMNS}; list the columns instead"
    columns = _source_columns(source, catalog)
    if columns is None:
        return (f"Reading every column of '{source.name}' {UNKNOWN_COLUMNS}; "
                "list the columns instead")
    for column in columns:
        candidates = _column_names(source, column)
        if any(fnmatch(c, p) for c in candidates for p in patterns):
            return f"Access to column '{candidates[1]}' is denied (read through '*')"
    return None
