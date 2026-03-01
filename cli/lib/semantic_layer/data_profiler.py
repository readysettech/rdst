"""
Data Profiler for Guided Schema Annotation

Collects column-level statistics (samples, nullability, value distributions)
to give the LLM rich context for annotation. Reuses the introspector's DB
connection pattern and pattern_detector for structural patterns.

Two SQL queries per table:
1. Stats query: counts, distinct counts, null fractions for all columns
2. Value frequencies: top values per text/enum column
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .pattern_detector import (
    detect_delimiter_columns_sql_postgres,
    detect_delimiter_columns_sql_mysql,
    DELIMITER_FRACTION_THRESHOLD,
)


@dataclass
class ColumnProfile:
    """Statistical profile for a single column."""

    name: str
    data_type: str
    sample_values: list[str] = field(default_factory=list)
    null_fraction: float = 0.0
    distinct_count: int = 0
    top_values: dict[str, int] = field(default_factory=dict)
    detected_pattern: str = ""  # e.g. "comma_separated_list"


@dataclass
class TableProfile:
    """Aggregate profile for a table — schema + data stats."""

    name: str
    row_estimate: int = 0
    row_estimate_str: str = "0"
    columns: dict[str, ColumnProfile] = field(default_factory=dict)
    sample_rows: list[dict] = field(default_factory=list)
    foreign_keys: list[str] = field(default_factory=list)


class DataProfiler:
    """
    Profiles tables by collecting column stats via SQL.

    Reuses the same connection-parameter pattern as SchemaIntrospector.
    """

    def __init__(self, target_config: dict):
        self.config = target_config
        self.engine = target_config.get("engine", "").lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def profile_table(
        self,
        table_name: str,
        columns: dict,
        row_estimate: int,
        row_estimate_str: str,
        relationships: list,
        sample_rows: int = 5,
    ) -> TableProfile:
        """Profile a single table using its existing semantic-layer metadata.

        Args:
            table_name: Table name.
            columns: dict[str, ColumnAnnotation] from the semantic layer.
            row_estimate: Numeric row estimate (for TABLESAMPLE sizing).
            row_estimate_str: Human-readable row estimate (e.g. "1.2M").
            relationships: List of Relationship objects for FK descriptions.
            sample_rows: Number of full rows to sample.

        Returns:
            TableProfile with per-column stats populated.
        """
        if self.engine in ("postgresql", "postgres"):
            return self._profile_postgres(
                table_name, columns, row_estimate, row_estimate_str,
                relationships, sample_rows,
            )
        elif self.engine == "mysql":
            return self._profile_mysql(
                table_name, columns, row_estimate, row_estimate_str,
                relationships, sample_rows,
            )
        else:
            raise ValueError(f"Unsupported engine: {self.engine}")

    # ------------------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------------------

    def _profile_postgres(
        self, table_name, columns, row_estimate, row_estimate_str,
        relationships, sample_rows,
    ) -> TableProfile:
        import psycopg2
        import psycopg2.extras

        conn = self._connect()
        try:
            profile = TableProfile(
                name=table_name,
                row_estimate=row_estimate,
                row_estimate_str=row_estimate_str,
                foreign_keys=[r.join_pattern for r in relationships],
            )

            with conn.cursor() as cur:
                col_names = list(columns.keys())
                col_types = {n: c.data_type for n, c in columns.items()}

                # 1. Stats: null fraction + approximate distinct count per column
                self._pg_column_stats(cur, table_name, col_names, col_types, row_estimate, profile)

                # 2. Top value frequencies for text / enum columns
                self._pg_top_values(cur, table_name, col_names, col_types, row_estimate, profile)

                # 3. Sample rows
                self._pg_sample_rows(cur, table_name, sample_rows, row_estimate, profile)

                # 4. Delimiter pattern detection
                self._pg_delimiter_patterns(cur, table_name, col_names, col_types, row_estimate, profile)

            return profile
        finally:
            conn.close()

    def _pg_column_stats(self, cur, table_name, col_names, col_types, row_estimate, profile):
        """Single query: null fraction + distinct count for every column."""
        if not col_names:
            return

        # Use TABLESAMPLE for large tables
        sample_clause = ""
        if row_estimate > 50_000:
            pct = min(100.0, max(0.5, (10_000 / row_estimate) * 100))
            sample_clause = f" TABLESAMPLE SYSTEM({pct})"

        parts = []
        for col in col_names:
            parts.append(
                f'COUNT("{col}") AS "{col}__cnt", '
                f'COUNT(DISTINCT "{col}") AS "{col}__dist", '
                f'SUM(CASE WHEN "{col}" IS NULL THEN 1 ELSE 0 END) AS "{col}__nulls"'
            )

        sql = f'SELECT COUNT(*) AS __total, {", ".join(parts)} FROM "{table_name}"{sample_clause}'
        cur.execute(sql)
        row = cur.fetchone()
        if not row:
            return

        total = row[0] or 1
        idx = 1
        for col in col_names:
            cnt = row[idx] or 0
            dist = row[idx + 1] or 0
            nulls = row[idx + 2] or 0
            idx += 3

            cp = profile.columns.setdefault(
                col, ColumnProfile(name=col, data_type=col_types.get(col, ""))
            )
            cp.null_fraction = round(nulls / total, 4) if total else 0.0
            cp.distinct_count = dist

    def _pg_top_values(self, cur, table_name, col_names, col_types, row_estimate, profile):
        """Top-10 value frequencies for text, enum, and small-int columns."""
        interesting = [
            c for c in col_names
            if col_types.get(c, "") in (
                "text", "string", "varchar", "character varying", "char", "enum",
                "int", "smallint", "tinyint", "boolean",
            )
        ]
        if not interesting:
            return

        sample_clause = ""
        if row_estimate > 50_000:
            pct = min(100.0, max(1.0, (20_000 / row_estimate) * 100))
            sample_clause = f" TABLESAMPLE SYSTEM({pct})"

        for col in interesting:
            try:
                cur.execute(
                    f'SELECT "{col}"::text, COUNT(*) AS cnt '
                    f'FROM "{table_name}"{sample_clause} '
                    f'WHERE "{col}" IS NOT NULL '
                    f'GROUP BY "{col}" ORDER BY cnt DESC LIMIT 10'
                )
                rows = cur.fetchall()
                cp = profile.columns.setdefault(
                    col, ColumnProfile(name=col, data_type=col_types.get(col, ""))
                )
                cp.top_values = {str(r[0]): r[1] for r in rows}
                cp.sample_values = [str(r[0]) for r in rows[:5]]
            except Exception:
                pass  # Skip columns that error (e.g. unsupported cast)

    def _pg_sample_rows(self, cur, table_name, sample_rows, row_estimate, profile):
        """Fetch a few full rows for context."""
        import psycopg2.extras

        sample_clause = ""
        if row_estimate > 50_000:
            sample_clause = " TABLESAMPLE SYSTEM(1)"

        try:
            cur2 = cur.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur2.execute(
                f'SELECT * FROM "{table_name}"{sample_clause} LIMIT {sample_rows}'
            )
            profile.sample_rows = [
                {k: _safe_str(v) for k, v in dict(row).items()}
                for row in cur2.fetchall()
            ]
            cur2.close()
        except Exception:
            pass

    def _pg_delimiter_patterns(self, cur, table_name, col_names, col_types, row_estimate, profile):
        """Detect comma-separated-list columns."""
        text_cols = [
            c for c in col_names
            if col_types.get(c, "") in ("text", "varchar", "string", "character varying")
            and not profile.columns.get(c, ColumnProfile(name=c, data_type="")).top_values
        ]
        if not text_cols:
            return
        try:
            sql = detect_delimiter_columns_sql_postgres(text_cols, table_name, row_estimate)
            if not sql:
                return
            cur.execute(sql)
            row = cur.fetchone()
            if row:
                for i, col in enumerate(text_cols):
                    fraction = row[i]
                    if fraction is not None and fraction > DELIMITER_FRACTION_THRESHOLD:
                        cp = profile.columns.setdefault(
                            col, ColumnProfile(name=col, data_type=col_types.get(col, ""))
                        )
                        cp.detected_pattern = "comma_separated_list"
        except Exception:
            pass

    # ------------------------------------------------------------------
    # MySQL
    # ------------------------------------------------------------------

    def _profile_mysql(
        self, table_name, columns, row_estimate, row_estimate_str,
        relationships, sample_rows,
    ) -> TableProfile:
        import pymysql
        import pymysql.cursors

        conn = self._connect()
        try:
            profile = TableProfile(
                name=table_name,
                row_estimate=row_estimate,
                row_estimate_str=row_estimate_str,
                foreign_keys=[r.join_pattern for r in relationships],
            )

            with conn.cursor() as cur:
                col_names = list(columns.keys())
                col_types = {n: c.data_type for n, c in columns.items()}

                self._mysql_column_stats(cur, table_name, col_names, col_types, row_estimate, profile)
                self._mysql_top_values(cur, table_name, col_names, col_types, row_estimate, profile)
                self._mysql_sample_rows(cur, table_name, sample_rows, row_estimate, profile)
                self._mysql_delimiter_patterns(cur, table_name, col_names, col_types, row_estimate, profile)

            return profile
        finally:
            conn.close()

    def _mysql_column_stats(self, cur, table_name, col_names, col_types, row_estimate, profile):
        if not col_names:
            return

        limit_clause = ""
        if row_estimate > 50_000:
            limit_clause = " LIMIT 10000"

        parts = []
        for col in col_names:
            parts.append(
                f'COUNT(`{col}`) AS `{col}__cnt`, '
                f'COUNT(DISTINCT `{col}`) AS `{col}__dist`, '
                f'SUM(CASE WHEN `{col}` IS NULL THEN 1 ELSE 0 END) AS `{col}__nulls`'
            )

        if limit_clause:
            inner_cols = ", ".join(f'`{c}`' for c in col_names)
            sql = (
                f'SELECT COUNT(*) AS __total, {", ".join(parts)} '
                f'FROM (SELECT {inner_cols} FROM `{table_name}`{limit_clause}) sampled'
            )
        else:
            sql = f'SELECT COUNT(*) AS __total, {", ".join(parts)} FROM `{table_name}`'

        cur.execute(sql)
        row = cur.fetchone()
        if not row:
            return

        total = row[0] or 1
        idx = 1
        for col in col_names:
            cnt = row[idx] or 0
            dist = row[idx + 1] or 0
            nulls = row[idx + 2] or 0
            idx += 3

            cp = profile.columns.setdefault(
                col, ColumnProfile(name=col, data_type=col_types.get(col, ""))
            )
            cp.null_fraction = round(nulls / total, 4) if total else 0.0
            cp.distinct_count = dist

    def _mysql_top_values(self, cur, table_name, col_names, col_types, row_estimate, profile):
        interesting = [
            c for c in col_names
            if col_types.get(c, "") in (
                "text", "varchar", "char", "enum",
                "int", "smallint", "tinyint", "boolean",
            )
        ]
        if not interesting:
            return

        for col in interesting:
            try:
                if row_estimate > 50_000:
                    cur.execute(
                        f'SELECT CAST(`{col}` AS CHAR) AS val, COUNT(*) AS cnt '
                        f'FROM (SELECT `{col}` FROM `{table_name}` LIMIT 20000) sampled '
                        f'WHERE `{col}` IS NOT NULL '
                        f'GROUP BY val ORDER BY cnt DESC LIMIT 10'
                    )
                else:
                    cur.execute(
                        f'SELECT CAST(`{col}` AS CHAR) AS val, COUNT(*) AS cnt '
                        f'FROM `{table_name}` '
                        f'WHERE `{col}` IS NOT NULL '
                        f'GROUP BY val ORDER BY cnt DESC LIMIT 10'
                    )
                rows = cur.fetchall()
                cp = profile.columns.setdefault(
                    col, ColumnProfile(name=col, data_type=col_types.get(col, ""))
                )
                cp.top_values = {str(r[0]): r[1] for r in rows}
                cp.sample_values = [str(r[0]) for r in rows[:5]]
            except Exception:
                pass

    def _mysql_sample_rows(self, cur, table_name, sample_rows, row_estimate, profile):
        import pymysql.cursors

        try:
            dict_cur = cur.connection.cursor(pymysql.cursors.DictCursor)
            dict_cur.execute(f'SELECT * FROM `{table_name}` LIMIT {sample_rows}')
            profile.sample_rows = [
                {k: _safe_str(v) for k, v in row.items()}
                for row in dict_cur.fetchall()
            ]
            dict_cur.close()
        except Exception:
            pass

    def _mysql_delimiter_patterns(self, cur, table_name, col_names, col_types, row_estimate, profile):
        text_cols = [
            c for c in col_names
            if col_types.get(c, "") in ("text", "varchar", "char")
            and not profile.columns.get(c, ColumnProfile(name=c, data_type="")).top_values
        ]
        if not text_cols:
            return
        try:
            sql = detect_delimiter_columns_sql_mysql(text_cols, table_name, row_estimate)
            if not sql:
                return
            cur.execute(sql)
            row = cur.fetchone()
            if row:
                for i, col in enumerate(text_cols):
                    fraction = row[i]
                    if fraction is not None and fraction > DELIMITER_FRACTION_THRESHOLD:
                        cp = profile.columns.setdefault(
                            col, ColumnProfile(name=col, data_type=col_types.get(col, ""))
                        )
                        cp.detected_pattern = "comma_separated_list"
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _connect(self):
        """Create a database connection using shared db_connection utility."""
        from lib.db_connection import resolve_connection_params

        params = resolve_connection_params(target_config=self.config)
        engine = params["engine"]

        if engine in ("postgresql", "postgres"):
            import psycopg2
            return psycopg2.connect(
                host=params["host"],
                port=params["port"],
                user=params["user"],
                password=params["password"],
                database=params["database"],
                sslmode=params["sslmode"],
                connect_timeout=10,
            )
        elif engine == "mysql":
            import pymysql
            connect_kwargs = {
                "host": params["host"],
                "port": params["port"],
                "user": params["user"],
                "password": params["password"],
                "database": params["database"],
                "connect_timeout": 10,
            }
            if params.get("tls"):
                connect_kwargs["ssl"] = {"ssl": True}
            return pymysql.connect(**connect_kwargs)
        else:
            raise ValueError(f"Unsupported engine: {engine}")


def _safe_str(v) -> str:
    """Stringify a value, truncating if very long."""
    if v is None:
        return "NULL"
    s = str(v)
    return s[:200] if len(s) > 200 else s
