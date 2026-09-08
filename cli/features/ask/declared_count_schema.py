"""Project existing Ask schema declarations into the count planner's structure."""
from collections.abc import Mapping

import sqlglot
from sqlglot import exp


def _value(value, *names, default=None):
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def count_schema(schema_info, dialect):
    """Keep all known columns, PK members and scalar relationship equalities.

    Ask's Relationship objects do not carry physical-constraint provenance.
    These are existing schema declarations; native reference/identity checks
    remain mandatory. No relationship is inferred from names or question text.
    """
    if dialect not in {"mysql", "postgres", "postgresql"}:
        raise ValueError("unsupported-count-dialect")
    dialect = "postgres" if dialect == "postgresql" else dialect
    raw_tables = _value(schema_info, "tables")
    if not isinstance(raw_tables, Mapping) or not raw_tables:
        raise ValueError("count-schema-unavailable")
    tables = {}
    for name, table in raw_tables.items():
        columns = _value(table, "columns")
        if not isinstance(name, str) or not name or not isinstance(columns, Mapping) or not columns:
            raise ValueError("invalid-count-table")
        fields, primary = {}, []
        for column, details in columns.items():
            if not isinstance(column, str) or not column:
                raise ValueError("invalid-count-column")
            data_type = _value(details, "data_type", "type", default="unknown")
            if not isinstance(data_type, str):
                raise ValueError("invalid-count-column-type")
            fields[column] = {"type": data_type}
            if _value(details, "is_primary_key", default=False) is True:
                primary.append(column)
        tables[name] = {"columns": fields, "primary_key": primary, "foreign_keys": []}
    for name, table in raw_tables.items():
        relationships = _value(table, "relationships", default=[])
        if not isinstance(relationships, (list, tuple)):
            raise ValueError("invalid-count-relationships")
        seen = set()
        for relationship in relationships:
            kind = _value(relationship, "relationship_type", "type")
            if kind not in {"many_to_one", "one_to_one"}:
                continue
            target = _value(relationship, "target_table", "target")
            join = _value(relationship, "join_pattern", "join")
            if target not in tables or not isinstance(join, str) or not join.strip():
                raise ValueError("incomplete-count-relationship")
            tokens = sqlglot.Dialect.get_or_raise(dialect).tokenize(join)
            if any(token.comments or token.token_type.name == "HINT" for token in tokens):
                raise ValueError("commented-count-relationship")
            trees = sqlglot.parse("SELECT " + join, read=dialect)
            if len(trees) != 1 or not isinstance(trees[0], exp.Select):
                raise ValueError("invalid-count-relationship-expression")
            tree = trees[0]
            if len(tree.expressions) != 1 or any(k != "expressions" and v for k, v in tree.args.items()):
                raise ValueError("modified-count-relationship")
            node = tree.expressions[0]
            while isinstance(node, exp.Paren):
                node = node.this
            if not isinstance(node, exp.EQ) or any(k not in {"this", "expression"} and v for k, v in node.args.items()):
                raise ValueError("count-relationship-needs-scalar-equality")
            pair = [node.this, node.expression]
            if any(not isinstance(c, exp.Column) or not isinstance(c.this, exp.Identifier)
                   or not isinstance(c.args.get("table"), exp.Identifier)
                   or any(k not in {"this", "table"} and v for k, v in c.args.items()) for c in pair):
                raise ValueError("count-relationship-needs-qualified-columns")
            if pair[0].table != name:
                pair.reverse()
            left, right = pair
            if name == target or left.table != name or right.table != target:
                raise ValueError("count-relationship-direction-unresolved")
            if left.name not in tables[name]["columns"] or right.name not in tables[target]["columns"]:
                raise ValueError("unknown-count-relationship-column")
            signature = (left.name, target, right.name)
            if signature not in seen:
                seen.add(signature)
                tables[name]["foreign_keys"].append({"columns": [left.name],
                    "referenced_table": target, "referenced_columns": [right.name]})
    return {"tables": tables}
