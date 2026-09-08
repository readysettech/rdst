"""Ground a requested state through one simple structural lookup relationship."""

from dataclasses import dataclass
import hashlib
import json
from time import perf_counter

import sqlglot
from sqlglot import exp
from .sql_validation import check_read_only
from .encoded_identifier_storage import _schema_table, _schema_column

VERSION = "proved-state-lookup-v6-numeric-text-proxy"
INTEGERS = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}
TEXT = {"char", "varchar", "text", "enum", "string"}
PROXY_TYPES = INTEGERS | {
    "decimal",
    "numeric",
    "double",
    "float",
    "real",
    "date",
    "datetime",
    "timestamp",
}


def kind(column):
    return str(getattr(column, "data_type", "")).lower().split("(", 1)[0]


def _nonnull_predicates(tree):
    where = tree.args.get("where")
    if where is None:
        return []
    return [
        n
        for n in where.walk()
        if isinstance(n, exp.Not)
        and n.find_ancestor(exp.Select) is tree
        and isinstance(n.this, exp.Is)
        and isinstance(n.this.this, exp.Column)
        and isinstance(n.this.expression, exp.Null)
    ]


def _proxy_predicates(tree):
    where = tree.args.get("where")
    if where is None:
        return []
    result = _nonnull_predicates(tree)
    for node in where.walk():
        if (
            not isinstance(node, exp.RegexpLike)
            or node.find_ancestor(exp.Select) is not tree
        ):
            continue
        if (
            not isinstance(node.this, exp.Column)
            or not isinstance(node.expression, exp.Literal)
            or not node.expression.is_string
            or node.expression.this != "^[0-9]+$"
        ):
            continue
        if any(
            value
            for key, value in node.args.items()
            if key not in {"this", "expression"}
        ):
            continue
        owner = node.parent
        while isinstance(owner, (exp.And, exp.Paren)):
            owner = owner.parent
        if owner is where:
            result.append(node)
    return result


@dataclass(frozen=True)
class StateLookupPlan:
    original_sql: str
    source_key_sql: str
    lookup_table: str
    lookup_key: str
    lookup_label: str
    relationship_sql: str

    def structural_facts(self):
        tree = sqlglot.parse_one(self.original_sql, read="mysql")
        return {
            (
                "numeric_text_predicate"
                if isinstance(_proxy_predicates(tree)[0], exp.RegexpLike)
                else "non_null_predicate"
            ): _proxy_predicates(tree)[0].sql(dialect="mysql"),
            "lookup_relationship": self.relationship_sql,
            "lookup_label": f"{self.lookup_table}.{self.lookup_label}",
        }

    def lookup_sql(self, state):
        if not isinstance(state, str) or not state.strip() or len(state) > 64:
            return None
        table = exp.Table(this=exp.to_identifier(self.lookup_table, quoted=True)).sql(
            dialect="mysql"
        )
        key = exp.column(self.lookup_key, table="_state", quoted=True).sql(
            dialect="mysql"
        )
        label = exp.column(self.lookup_label, table="_state", quoted=True).sql(
            dialect="mysql"
        )
        other_key = exp.column(self.lookup_key, table="_other", quoted=True).sql(
            dialect="mysql"
        )
        literal = exp.Literal.string(state).sql(dialect="mysql")
        return f"SELECT {key}, {label}, (SELECT COUNT(*) FROM {table} AS _other WHERE {other_key}={key}) FROM {table} AS _state WHERE LOWER({label})=LOWER({literal}) LIMIT 2"

    def state_only_sql(self, key):
        if not valid_integer(key):
            return None
        tree = sqlglot.parse_one(self.original_sql, read="mysql")
        _proxy_predicates(tree)[0].replace(
            exp.EQ(
                this=sqlglot.parse_one(self.source_key_sql, read="mysql"),
                expression=exp.Literal.number(key),
            )
        )
        return tree.sql(dialect="mysql")

    def candidate_sql(self, key):
        state_only = self.state_only_sql(key)
        if state_only is None:
            return None
        original = sqlglot.parse_one(self.original_sql, read="mysql")
        predicate = _proxy_predicates(original)[0].copy()
        tree = sqlglot.parse_one(state_only, read="mysql")
        tree.set(
            "where",
            exp.Where(this=exp.And(this=tree.args["where"].this, expression=predicate)),
        )
        return tree.sql(dialect="mysql")

    def scope_proof_sql(self, key):
        original = sqlglot.parse_one(self.original_sql, read="mysql")
        predicate = _proxy_predicates(original)[0]
        column = (
            None
            if isinstance(predicate, exp.RegexpLike)
            else predicate.this.this.copy()
        )
        candidate = self.state_only_sql(key)
        if candidate is None:
            return None
        tree = sqlglot.parse_one(candidate, read="mysql")
        projection = tree.expressions[0]
        count = projection.this if isinstance(projection, exp.Alias) else projection
        tree.set(
            "expressions",
            [
                count.copy(),
                exp.Count(this=exp.Star()),
                (
                    exp.Count(
                        this=exp.Case(
                            ifs=[
                                exp.If(
                                    this=predicate.copy(), true=exp.Literal.number(1)
                                )
                            ],
                            default=exp.Null(),
                        )
                    )
                    if column is None
                    else exp.Count(this=column)
                ),
            ],
        )
        return tree.sql(dialect="mysql")


def valid_integer(value):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and -(2**53) <= value <= 2**53
    )


def _preserved_scalar_scope(nested, tree, schema_info):
    """Admit one immutable uncorrelated dimension-key equality scope."""
    if not isinstance(nested.parent, exp.Subquery):
        return False
    wrapper = nested.parent
    equality = wrapper.parent
    if not isinstance(equality, exp.EQ):
        return False
    outer = equality.expression if equality.this is wrapper else equality.this
    if (
        not isinstance(outer, exp.Column)
        or not outer.table
        or outer.db
        or outer.catalog
    ):
        return False
    owner = equality.parent
    while isinstance(owner, (exp.And, exp.Paren)):
        owner = owner.parent
    if owner is not tree.args.get("where"):
        return False
    if any(
        v for k, v in nested.args.items() if k not in {"expressions", "from_", "where"}
    ):
        return False
    source = nested.args.get("from_")
    where = nested.args.get("where")
    if source is None or not isinstance(source.this, exp.Table) or where is None:
        return False
    table = source.this
    if table.db or table.catalog or len(nested.expressions) != 1:
        return False
    schema = _schema_table(schema_info, table.name)
    if schema is None:
        return False
    output = nested.expressions[0]
    if not isinstance(output, exp.Column):
        return False
    info = _schema_column(schema, output.name)
    if kind(info) not in INTEGERS:
        return False
    outer_tables = [
        t
        for t in tree.find_all(exp.Table)
        if t.find_ancestor(exp.Select) is tree
        and t.alias_or_name.casefold() == outer.table.casefold()
    ]
    if len(outer_tables) != 1:
        return False
    outer_table = outer_tables[0]
    outer_schema = _schema_table(schema_info, outer_table.name)
    if (
        outer_schema is None
        or kind(_schema_column(outer_schema, outer.name)) not in INTEGERS
    ):
        return False
    relationship_found = False
    for relationship in getattr(outer_schema, "relationships", ()):
        if isinstance(relationship, dict):
            target, join, relation_type = (
                relationship.get("target"),
                relationship.get("join"),
                relationship.get("type"),
            )
        else:
            target, join, relation_type = (
                getattr(relationship, "target_table", None),
                getattr(relationship, "join_pattern", None),
                getattr(relationship, "relationship_type", None),
            )
        if (
            relation_type != "many_to_one"
            or not isinstance(target, str)
            or target.casefold() != table.name.casefold()
            or not isinstance(join, str)
        ):
            continue
        try:
            relation = sqlglot.parse_one(join, read="mysql")
        except sqlglot.errors.ParseError:
            continue
        if not isinstance(relation, exp.EQ) or not all(
            isinstance(c, exp.Column) and not c.db and not c.catalog
            for c in (relation.this, relation.expression)
        ):
            continue
        actual = {
            (c.table.casefold(), c.name.casefold())
            for c in (relation.this, relation.expression)
        }
        expected = {
            (outer_table.name.casefold(), outer.name.casefold()),
            (table.name.casefold(), output.name.casefold()),
        }
        relationship_found |= len(actual) == 2 and actual == expected
    if not relationship_found:
        return False
    for column in nested.find_all(exp.Column):
        if (
            column.db
            or column.catalog
            or column.table
            and column.table.casefold() != table.alias_or_name.casefold()
        ):
            return False
        if _schema_column(schema, column.name) is None:
            return False
    conditions = (
        where.this.flatten() if isinstance(where.this, exp.And) else [where.this]
    )
    for condition in conditions:
        if not isinstance(condition, exp.EQ):
            return False
        operands = [condition.this, condition.expression]
        if (
            sum(isinstance(x, exp.Column) for x in operands) != 1
            or sum(isinstance(x, exp.Literal) for x in operands) != 1
        ):
            return False
    return True


def plan_state_lookup(sql, dialect, schema_info):
    if (
        dialect != "mysql"
        or schema_info is None
        or not check_read_only(sql)["is_read_only"]
    ):
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = statements[0]
    selects = list(tree.find_all(exp.Select))
    if len(tree.expressions) != 1 or not 1 <= len(selects) <= 2:
        return None
    if len(selects) == 2:
        nested = next(s for s in selects if s is not tree)
        if not _preserved_scalar_scope(nested, tree, schema_info):
            if (
                not isinstance(nested.parent, exp.Exists)
                or nested.expressions != [exp.Literal.number(1)]
                or nested.args.get("from_") is None
                or not isinstance(nested.args["from_"].this, exp.Table)
                or nested.args.get("where") is None
                or any(
                    v
                    for k, v in nested.args.items()
                    if k not in {"expressions", "from_", "where"}
                )
            ):
                return None
            owner = nested.parent.parent
            while isinstance(owner, exp.And):
                owner = owner.parent
            if owner is not tree.args.get("where"):
                return None
            # Admit only positive equality scopes. The predicate is kept intact;
            # this does not decide that an EXISTS condition is redundant.
            condition = nested.args["where"].this
            predicates = (
                condition.flatten() if isinstance(condition, exp.And) else [condition]
            )
            for predicate in predicates:
                if not isinstance(predicate, exp.EQ) or not all(
                    isinstance(operand, (exp.Column, exp.Literal))
                    for operand in (predicate.this, predicate.expression)
                ):
                    return None
    if any(
        tree.args.get(k)
        for k in (
            "group",
            "having",
            "distinct",
            "with_",
            "qualify",
            "locks",
            "into",
            "order",
            "limit",
            "offset",
        )
    ):
        return None
    projection = tree.expressions[0]
    count = projection.this if isinstance(projection, exp.Alias) else projection
    if not isinstance(count, exp.Count):
        return None
    argument = count.this
    if isinstance(argument, exp.Distinct):
        if len(argument.expressions) != 1 or not isinstance(
            argument.expressions[0], exp.Column
        ):
            return None
    elif not isinstance(argument, (exp.Column, exp.Star)):
        return None
    predicates = _proxy_predicates(tree)
    if len(predicates) != 1:
        return None
    predicate = predicates[0]
    if any(
        isinstance(n, exp.Or) or isinstance(n, exp.Not) and n is not predicate
        for n in tree.walk()
    ):
        return None
    if any(
        isinstance(n, exp.Func)
        and n is not count
        and n is not predicate
        and not isinstance(n, (exp.And, exp.Not, exp.Exists))
        for n in tree.walk()
    ):
        return None
    numeric_text = isinstance(predicate, exp.RegexpLike)
    column = predicate.this if numeric_text else predicate.this.this
    if (
        numeric_text
        and sum(
            t.alias_or_name.casefold() == column.table.casefold()
            for t in tree.find_all(exp.Table)
            if t.find_ancestor(exp.Select) is tree
        )
        != 1
    ):
        return None
    if column.db or column.catalog:
        return None
    tables = list(tree.find_all(exp.Table))
    if not 1 <= len(tables) <= 4 or any(t.db or t.catalog for t in tables):
        return None
    aliases = {t.alias_or_name.casefold(): t.name for t in tables}
    if (
        len(aliases) != len(tables)
        or not column.table
        or column.table.casefold() not in aliases
    ):
        return None
    source_name = aliases[column.table.casefold()]
    source = _schema_table(schema_info, source_name)
    if source is None or kind(_schema_column(source, column.name)) not in (
        TEXT if numeric_text else PROXY_TYPES
    ):
        return None
    candidates = []
    for relationship in getattr(source, "relationships", ()):
        if isinstance(relationship, dict):
            target_name, join, relation_type = (
                relationship.get("target"),
                relationship.get("join"),
                relationship.get("type"),
            )
        else:
            target_name, join, relation_type = (
                getattr(relationship, "target_table", None),
                getattr(relationship, "join_pattern", None),
                getattr(relationship, "relationship_type", None),
            )
        if (
            relation_type != "many_to_one"
            or not isinstance(join, str)
            or not isinstance(target_name, str)
        ):
            continue
        if target_name.casefold() in {t.name.casefold() for t in tables}:
            continue
        target = _schema_table(schema_info, target_name)
        if target is None or len(target.columns) != 2:
            continue
        try:
            equality = sqlglot.parse_one(join, read="mysql")
        except sqlglot.errors.ParseError:
            continue
        if not isinstance(equality, exp.EQ) or not all(
            isinstance(c, exp.Column) and not c.db and not c.catalog
            for c in (equality.this, equality.expression)
        ):
            continue
        a, b = equality.this, equality.expression
        if a.table.casefold() == target_name.casefold():
            a, b = b, a
        if (
            a.table.casefold() != source_name.casefold()
            or b.table.casefold() != target_name.casefold()
        ):
            continue
        if (
            kind(_schema_column(source, a.name)) not in INTEGERS
            or kind(_schema_column(target, b.name)) not in INTEGERS
        ):
            continue
        labels = [
            name
            for name, item in target.columns.items()
            if name.casefold() != b.name.casefold() and kind(item) in TEXT
        ]
        if len(labels) != 1:
            continue
        key = exp.column(a.name, table=column.table, quoted=True).sql(dialect="mysql")
        candidates.append(
            StateLookupPlan(
                sql, key, target_name, b.name, labels[0], equality.sql(dialect="mysql")
            )
        )
    return candidates[0] if len(candidates) == 1 else None


def proven_state_key(rows, state):
    if len(rows) != 1 or len(rows[0]) != 3:
        return None
    key, label, key_count = rows[0]
    if (
        not valid_integer(key)
        or not isinstance(label, str)
        or not isinstance(state, str)
    ):
        return None
    if (
        not valid_integer(key_count)
        or key_count != 1
        or label.casefold() != state.casefold()
    ):
        return None
    return key


def proven_state_count(rows, before):
    if len(rows) != 1 or len(rows[0]) != 3 or len(before) != 1 or len(before[0]) != 1:
        return None
    count, total, nonnull = rows[0]
    old = before[0][0]
    if not all(valid_integer(v) and v >= 0 for v in (count, total, nonnull, old)):
        return None
    return count if count <= total == nonnull and count <= old else None


def accepts_state_count(expected, rows):
    return (
        expected is not None
        and len(rows) == 1
        and len(rows[0]) == 1
        and valid_integer(rows[0][0])
        and rows[0][0] == expected
    )


SYSTEM = "Classify a requested categorical state and explicit non-null requirements from effective_question. Interpret any language and ordinary typos. Use parsed structural facts to identify a possible state lookup, not to infer a missing request. Do not generate SQL or select a database code. The host independently checks an exact stored label and the affected row population."


CATALOG = [
    {
        "intent": "explicit_state_lookup",
        "claim": "lookup_describes_requested_state is true only when the question requests a specific categorical lifecycle or outcome state and the supplied foreign-key lookup label describes that state. It is false for an entity name, a title, a raw measurement, a vague business judgment, or uncertain meanings. nonnull_value_requested is true if the question explicitly asks that the field in non_null_predicate has a value or otherwise requests that actual field's presence; this includes a requested state combined with that presence requirement. A specific state alone does not explicitly request a non-null numeric position or timestamp. state_excerpt must be the shortest exact contiguous excerpt naming the requested state in the question, excluding entity names and surrounding words; return an empty string when no specific state is requested. Do not translate, stem or invent a lookup label. The host will accept only a unique stored label matching this excerpt under case folding, verify a unique lookup key, and prove that all rows in the candidate state already meet the old non-null condition. It preserves all other conditions and the exact counting grain. Independently classify state_is_name_or_title as true if the state_excerpt is used as an entity name, job title, heading or arbitrary identifier rather than a lifecycle/outcome state. A job whose title is Completed is not thereby completed. Use true when this role is ambiguous. Only a categorical lifecycle/outcome value has state_is_name_or_title=false. If any proof fails, preserve the primary query.",
    }
]


SCHEMA = {
    "type": "object",
    "properties": {
        "lookup_describes_requested_state": {"type": "boolean"},
        "nonnull_value_requested": {"type": "boolean"},
        "state_excerpt": {"type": "string"},
        "state_is_name_or_title": {"type": "boolean"},
    },
    "required": [
        "lookup_describes_requested_state",
        "nonnull_value_requested",
        "state_excerpt",
        "state_is_name_or_title",
    ],
    "additionalProperties": False,
}


def route_state_lookup(q, sql, d, a, callback=None, *, structural_facts):
    facts = structural_facts
    catalog, system = CATALOG, SYSTEM
    if "numeric_text_predicate" in facts:
        system = SYSTEM.replace(
            "explicit non-null requirements", "explicit original-predicate requirements"
        )
        catalog = [
            {
                **item,
                "claim": item["claim"]
                .replace("non_null_predicate", "numeric_text_predicate")
                .replace(
                    "all rows in the candidate state already meet the old non-null condition",
                    "all rows in the candidate state already meet the original numeric-text predicate",
                ),
            }
            for item in CATALOG
        ]
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": catalog,
            "parsed_sql_facts": facts,
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = a.generate_response(
        prompt=prompt,
        system_message=system,
        purpose="state_lookup_routing",
        temperature=0.0,
        max_tokens=700,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "state_lookup",
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
    ex = v.get("state_excerpt")
    activate = (
        isinstance(ex, str)
        and bool(ex.strip())
        and ex in q
        and v.get("lookup_describes_requested_state") is True
        and v.get("state_is_name_or_title") is False
    )
    return {
        "activate": activate,
        "structural_facts": structural_facts,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
