"""
Pattern Detector for Column Value Analysis

Detects structural patterns in text columns that affect how the LLM
should generate SQL — specifically, delimiter-separated lists that
require splitting (string_to_array, unnest) rather than direct joins.

Used by the introspector to auto-enrich the semantic layer during
schema init/refresh. Detection uses SQL aggregation (not random
sampling) for deterministic, reliable results.
"""


def detect_delimiter_columns_sql_postgres(
    text_columns: list[str], table_name: str, row_estimate: int,
) -> str:
    """Build a SQL query that checks text columns for delimiter patterns.

    Returns a single query that, for each text column, computes the
    fraction of non-null rows containing a comma. If >10% of rows
    contain commas, the column is likely a delimiter-separated list.

    Uses TABLESAMPLE for large tables to keep the query fast.

    Args:
        text_columns: Names of text-type columns to check.
        table_name: Table name.
        row_estimate: Estimated row count (for TABLESAMPLE sizing).

    Returns:
        SQL string, or empty string if no text columns to check.
    """
    if not text_columns:
        return ""

    # For large tables, sample ~10K rows (enough for reliable fraction estimate)
    sample_clause = ""
    if row_estimate > 50_000:
        pct = min(100.0, max(0.5, (10_000 / row_estimate) * 100))
        sample_clause = f" TABLESAMPLE SYSTEM({pct})"

    agg_parts = []
    for col in text_columns:
        agg_parts.append(
            f'SUM(CASE WHEN "{col}" LIKE \'%,%\' THEN 1 ELSE 0 END)::float '
            f'/ NULLIF(COUNT("{col}"), 0) AS "{col}"'
        )

    return (
        f'SELECT {", ".join(agg_parts)} '
        f'FROM "{table_name}"{sample_clause}'
    )


def detect_delimiter_columns_sql_mysql(
    text_columns: list[str], table_name: str, row_estimate: int,
) -> str:
    """Build a SQL query that checks text columns for delimiter patterns (MySQL).

    Same logic as the Postgres variant but using MySQL syntax.
    """
    if not text_columns:
        return ""

    # MySQL lacks TABLESAMPLE; use a LIMIT subquery for large tables
    inner_limit = ""
    if row_estimate > 50_000:
        inner_limit = " LIMIT 10000"

    agg_parts = []
    for col in text_columns:
        agg_parts.append(
            f"SUM(CASE WHEN `{col}` LIKE '%,%' THEN 1 ELSE 0 END) "
            f"/ NULLIF(COUNT(`{col}`), 0) AS `{col}`"
        )

    if inner_limit:
        return (
            f'SELECT {", ".join(agg_parts)} '
            f'FROM (SELECT {", ".join(f"`{c}`" for c in text_columns)} '
            f'FROM `{table_name}`{inner_limit}) sampled'
        )
    return (
        f'SELECT {", ".join(agg_parts)} '
        f'FROM `{table_name}`'
    )


# Fraction threshold: if >10% of non-null values contain commas,
# mark the column as comma_separated_list.
DELIMITER_FRACTION_THRESHOLD = 0.10
