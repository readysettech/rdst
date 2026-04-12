"""
PostgreSQL metadata collection utilities.

Shared functions for collecting extension and custom type information
from PostgreSQL databases. Used by both:
- semantic_layer/introspector.py (rdst schema init)
- functions/schema_collector.py (rdst analyze)
"""

from typing import List, Tuple, Optional


def fetch_postgres_extensions(cursor) -> List[Tuple[str, str, str, str]]:
    """
    Fetch installed PostgreSQL extensions with descriptions and types.

    Args:
        cursor: Active database cursor

    Returns:
        List of tuples: (name, version, description, types_csv)
        types_csv is comma-separated list of types provided by the extension
    """
    cursor.execute("""
        SELECT
            e.extname,
            e.extversion,
            COALESCE(d.description, '') as description,
            COALESCE(
                (SELECT string_agg(t.typname, ', ' ORDER BY t.typname)
                 FROM pg_type t
                 JOIN pg_depend dep ON dep.objid = t.oid
                 WHERE dep.refobjid = e.oid
                   AND dep.deptype = 'e'
                   AND t.typname NOT LIKE '\\_%'),
                ''
            ) as types_provided
        FROM pg_extension e
        LEFT JOIN pg_catalog.pg_description d
            ON d.objoid = e.oid
            AND d.classoid = 'pg_catalog.pg_extension'::regclass
        WHERE e.extname NOT IN ('plpgsql')
        ORDER BY e.extname
    """)
    return cursor.fetchall()


def fetch_postgres_custom_types(cursor) -> List[Tuple[str, str, str, Optional[str], str]]:
    """
    Fetch custom types and domains from PostgreSQL.

    Excludes table row types (every table has an auto-generated composite type).

    Args:
        cursor: Active database cursor

    Returns:
        List of tuples: (name, type_code, category, base_type, enum_values_csv)
        - type_code: 'e'=enum, 'd'=domain, 'b'=base, 'c'=composite
        - category: 'enum', 'domain', 'base', 'composite'
        - base_type: for domains, the underlying type
        - enum_values_csv: comma-separated enum values
    """
    cursor.execute("""
        SELECT
            t.typname,
            t.typtype,
            CASE t.typtype
                WHEN 'c' THEN 'composite'
                WHEN 'd' THEN 'domain'
                WHEN 'e' THEN 'enum'
                WHEN 'b' THEN 'base'
                ELSE 'other'
            END as type_category,
            pg_catalog.format_type(t.typbasetype, t.typtypmod) as base_type,
            COALESCE(
                (SELECT string_agg(e.enumlabel, ', ' ORDER BY e.enumsortorder)
                 FROM pg_enum e WHERE e.enumtypid = t.oid),
                ''
            ) as enum_values
        FROM pg_type t
        JOIN pg_namespace n ON t.typnamespace = n.oid
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND n.nspname NOT LIKE 'pg\\_temp\\_%'
          AND t.typtype IN ('c', 'd', 'e', 'b')
          AND t.typname NOT LIKE '\\_%'
          -- Exclude composite types that are table row types
          AND NOT (t.typtype = 'c' AND EXISTS (
              SELECT 1 FROM pg_class c
              WHERE c.relname = t.typname
                AND c.relnamespace = t.typnamespace
                AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          ))
        ORDER BY t.typtype, t.typname
    """)
    return cursor.fetchall()

