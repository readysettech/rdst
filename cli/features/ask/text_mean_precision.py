"""Prove numeric text before removing unrequested decimal input quantization."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from time import perf_counter

import sqlglot
from sqlglot import exp

from .encoded_identifier_storage import _schema_table, _schema_column, _is_text_column
from .sql_validation import check_read_only

VERSION = "mysql-text-mean-precision-v1"
NUMERIC_TEXT_PATTERN = "^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$"


@dataclass(frozen=True)
class TextMeanPlan:
    candidate_sql: str
    proof_sql: str
    scale: int


def plan_text_mean(sql, dialect, schema_info):
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
    if len(tree.expressions) != 1 or len(list(tree.find_all(exp.Select))) != 1:
        return None
    if any(
        tree.args.get(k)
        for k in ("group", "having", "limit", "offset", "order", "distinct", "with_")
    ):
        return None
    projection = tree.expressions[0]
    avg = projection.this if isinstance(projection, exp.Alias) else projection
    if (
        not isinstance(avg, exp.Avg)
        or not isinstance(avg.this, exp.Cast)
        or not isinstance(avg.this.this, exp.Column)
    ):
        return None
    cast = avg.this
    column = cast.this
    kind = cast.args.get("to")
    if not isinstance(kind, exp.DataType) or kind.this != exp.DataType.Type.DECIMAL:
        return None
    try:
        params = [int(p.this.this) for p in kind.expressions]
    except (ValueError, AttributeError, TypeError):
        return None
    if len(params) != 2:
        return None
    precision, scale = params
    if not 1 <= precision <= 15 or not 0 <= scale <= min(6, precision):
        return None
    for node in tree.walk():
        if (
            not isinstance(node, exp.Func)
            or node is avg
            or node is cast
            or isinstance(node, (exp.And, exp.Or, exp.Not))
        ):
            continue
        if (
            isinstance(node, exp.TsOrDsToDate)
            and isinstance(node.this, exp.Column)
            and node.find_ancestor(exp.Where, exp.Join) is not None
        ):
            continue
        return None
    matches = []
    for ref in tree.find_all(exp.Table):
        if ref.db or ref.catalog:
            return None
        if column.table and column.table.casefold() != ref.alias_or_name.casefold():
            continue
        table = _schema_table(schema_info, ref.name)
        if table is None:
            return None
        item = _schema_column(table, column.name)
        if item is not None:
            matches.append(item)
    if len(matches) != 1 or not _is_text_column(matches[0]):
        return None
    col = column.sql(dialect="mysql")
    pattern = exp.Literal.string(NUMERIC_TEXT_PATTERN).sql(dialect="mysql")
    proof = tree.copy()
    expressions = sqlglot.parse_one(
        f"SELECT COUNT({col}), SUM(CASE WHEN CHAR_LENGTH(TRIM({col})) <= 32 AND TRIM({col}) REGEXP {pattern} THEN 1 ELSE 0 END), MAX(ABS(CAST({col} AS DOUBLE)))",
        read="mysql",
    ).expressions
    proof.set("expressions", expressions)
    cast.set("to", exp.DataType.build("DOUBLE"))
    return TextMeanPlan(tree.sql(dialect="mysql"), proof.sql(dialect="mysql"), scale)


def safe_text_mean_proof(rows, scale):
    if len(rows) != 1 or len(rows[0]) != 3:
        return False
    count, valid, largest = rows[0]
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not 0 < count <= 1_000_000
        or valid != count
    ):
        return False
    try:
        largest = float(largest)
    except (ValueError, TypeError, OverflowError):
        return False
    if not math.isfinite(largest) or largest < 0 or count * largest >= 2**50:
        return False
    # Bound accumulated floating error below a quarter of the original input
    # quantization. Large or ill-conditioned representations fail closed.
    return 4 * math.ulp(largest) * (count + 1) <= 0.25 * 10 ** (-scale)


def accepts_text_mean_result(original, candidate, proof, scale):
    if not safe_text_mean_proof(proof, scale):
        return False
    if (
        len(original) != 1
        or len(candidate) != 1
        or len(original[0]) != 1
        or len(candidate[0]) != 1
    ):
        return False
    before, after = original[0][0], candidate[0][0]
    if (
        not isinstance(before, Decimal)
        or not before.is_finite()
        or not isinstance(after, float)
        or not math.isfinite(after)
    ):
        return False
    count, _, largest = proof[0]
    tolerance = (
        Decimal(5).scaleb(-scale - 1)
        + Decimal(5).scaleb(-scale - 5)
        + Decimal.from_float(4 * math.ulp(float(largest)) * (count + 1))
    )
    return abs(before - Decimal.from_float(after)) <= tolerance


def route_text_mean_precision(question, sql, dialect, llm_manager, callback=None):
    prompt = json.dumps(
        {
            "effective_question": question,
            "generated_sql": sql,
            "dialect": dialect,
            "trigger_catalog": [
                {
                    "intent": "unrounded_numeric_text_mean",
                    "claim": "The request asks for an unrounded arithmetic mean of a count, score, physical measurement or duration. The SQL casts its text source to fixed decimal precision. A floating average of the original numeric text is appropriate when that input quantization was not requested. The host will prove canonical numeric storage and a safe arithmetic range, preserve the population and null exclusion, and require result agreement within the original input quantization. Monetary amounts must retain decimal arithmetic even without an explicit precision request. Counts of financial objects and durations are quantities rather than money. Abstain for money, identifiers, explicit decimal precision, unknown dimensions or uncertain meaning.",
                }
            ],
            "parsed_sql_facts": [
                "single scalar AVG of a text column cast to DECIMAL; only the cast target would change"
            ],
        },
        ensure_ascii=False,
    )
    start = perf_counter()
    response = llm_manager.generate_response(
        prompt=prompt,
        system_message="Decide whether the one listed numeric representation correction is appropriate. Interpret any language and typos. Do not assume the SQL is wrong. Return only the requested decision, the dimension of the averaged value, and a verbatim supporting excerpt from effective_question. Never write SQL or quote the catalog as evidence.",
        purpose="text_mean_precision_routing",
        temperature=0.0,
        max_tokens=700,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "text_mean_precision",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["activate", "abstain"],
                            },
                            "measure_kind": {
                                "type": "string",
                                "enum": ["quantity", "money", "identifier", "unknown"],
                            },
                            "source_excerpt": {"type": "string"},
                        },
                        "required": ["decision", "measure_kind", "source_excerpt"],
                        "additionalProperties": False,
                    },
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
            latency_ms=(perf_counter() - start) * 1000,
            model=response.get("model", "unknown"),
        )
    decision = json.loads(raw)
    excerpt = decision.get("source_excerpt", "")
    return {
        "activate": decision.get("decision") == "activate"
        and decision.get("measure_kind") == "quantity"
        and isinstance(excerpt, str)
        and bool(excerpt.strip())
        and excerpt in question,
        "measure_kind": decision.get("measure_kind"),
        "source_excerpt": excerpt,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
