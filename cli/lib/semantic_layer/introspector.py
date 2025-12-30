"""
Database Schema Introspector

Introspects database schema to bootstrap a semantic layer with:
- All tables and columns
- Data types and constraints
- Foreign key relationships
- Potential enum columns (detected by low cardinality)
"""

from typing import Optional
import os

from ..data_structures.semantic_layer import (
    SemanticLayer,
    TableAnnotation,
    ColumnAnnotation,
    Relationship,
    Extension,
    CustomType
)


class SchemaIntrospector:
    """
    Introspects database schema to bootstrap a semantic layer.

    Connects to the database and extracts:
    - Table names and estimated row counts
    - Column names, types, and nullability
    - Foreign key relationships
    - Potential enum columns (low distinct value count)
    """

    def __init__(self, target_config: dict):
        """
        Initialize introspector with target configuration.

        Args:
            target_config: Database configuration dict with:
                - engine: 'postgresql' or 'mysql'
                - host, port, user, database
                - password_env: environment variable for password
        """
        self.config = target_config
        self.engine = target_config.get('engine', '').lower()

    def _is_enum_column_name(self, col_name: str) -> bool:
        """
        Check if column name suggests it might be an enum.

        Patterns that suggest enum-like columns:
        - *typeid, *type (e.g., posttypeid, votetypeid)
        - class, status, state, category, level, grade
        - *kind, *mode, *flag
        """
        col_lower = col_name.lower()

        # Suffix patterns
        enum_suffixes = ['typeid', 'type', 'kind', 'mode', 'flag', 'status', 'state']
        for suffix in enum_suffixes:
            if col_lower.endswith(suffix):
                return True

        # Exact matches
        enum_names = ['class', 'category', 'level', 'grade', 'rank', 'tier', 'priority']
        if col_lower in enum_names:
            return True

        return False

    def introspect(self, target_name: str,
                   enum_threshold: int = 20,
                   sample_enums: bool = True) -> SemanticLayer:
        """
        Introspect database and create a semantic layer skeleton.

        Args:
            target_name: Name for the target in the semantic layer
            enum_threshold: Max distinct values to consider as enum
            sample_enums: Whether to sample enum values from database

        Returns:
            SemanticLayer with tables, columns, relationships populated

        Raises:
            ConnectionError: If cannot connect to database
            ValueError: If unsupported database engine
        """
        if self.engine in ['postgresql', 'postgres']:
            return self._introspect_postgres(target_name, enum_threshold, sample_enums)
        elif self.engine == 'mysql':
            return self._introspect_mysql(target_name, enum_threshold, sample_enums)
        else:
            raise ValueError(f"Unsupported database engine: {self.engine}")

    def _get_connection_params(self) -> dict:
        """Get connection parameters from config."""
        password_env = self.config.get('password_env')
        password = os.environ.get(password_env) if password_env else None

        return {
            'host': self.config.get('host'),
            'port': self.config.get('port'),
            'user': self.config.get('user'),
            'password': password,
            'database': self.config.get('database')
        }

    def _introspect_postgres(self, target_name: str,
                             enum_threshold: int,
                             sample_enums: bool) -> SemanticLayer:
        """Introspect PostgreSQL database."""
        import psycopg2

        params = self._get_connection_params()

        if not all([params['host'], params['user'], params['database'], params['password']]):
            raise ConnectionError("Missing database connection details")

        # Determine SSL mode based on config
        tls_enabled = self.config.get('tls', False)
        sslmode = 'prefer' if tls_enabled else 'disable'

        connection = psycopg2.connect(
            host=params['host'],
            port=params['port'] or 5432,
            user=params['user'],
            password=params['password'],
            database=params['database'],
            sslmode=sslmode,
            connect_timeout=10
        )

        layer = SemanticLayer(target=target_name)

        try:
            with connection.cursor() as cursor:
                # Get all tables
                cursor.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """)
                tables = [row[0] for row in cursor.fetchall()]

                for table_name in tables:
                    table_annotation = self._introspect_postgres_table(
                        cursor, table_name, enum_threshold, sample_enums
                    )
                    layer.tables[table_name] = table_annotation

                # Get foreign key relationships
                self._add_postgres_relationships(cursor, layer)

                # Get installed extensions and custom types
                self._add_postgres_extensions(cursor, layer)
                self._add_postgres_custom_types(cursor, layer)

        finally:
            connection.close()

        return layer

    def _introspect_postgres_table(self, cursor, table_name: str,
                                   enum_threshold: int,
                                   sample_enums: bool) -> TableAnnotation:
        """Introspect a single PostgreSQL table."""
        # Get row estimate
        cursor.execute("""
            SELECT reltuples::bigint as row_estimate
            FROM pg_class
            WHERE relname = %s
        """, (table_name,))
        result = cursor.fetchone()
        row_estimate = result[0] if result else 0

        # Format row estimate
        if row_estimate >= 1_000_000:
            row_str = f"{row_estimate / 1_000_000:.1f}M"
        elif row_estimate >= 1_000:
            row_str = f"{row_estimate / 1_000:.1f}K"
        else:
            row_str = str(row_estimate)

        table = TableAnnotation(
            name=table_name,
            description="",  # User will fill in
            row_estimate=row_str
        )

        # Get columns
        # Use udt_name for actual type name (data_type returns 'USER-DEFINED' for custom types)
        cursor.execute("""
            SELECT
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.column_default,
                c.character_maximum_length,
                c.numeric_precision,
                tc.constraint_type
            FROM information_schema.columns c
            LEFT JOIN information_schema.key_column_usage kcu
                ON c.table_name = kcu.table_name
                AND c.column_name = kcu.column_name
            LEFT JOIN information_schema.table_constraints tc
                ON kcu.constraint_name = tc.constraint_name
                AND tc.constraint_type = 'PRIMARY KEY'
            WHERE c.table_name = %s
            ORDER BY c.ordinal_position
        """, (table_name,))

        columns = cursor.fetchall()

        for col in columns:
            col_name, data_type, udt_name, is_nullable, default, char_len, num_prec, constraint = col

            # Determine column type - use udt_name for USER-DEFINED types
            col_type = self._normalize_postgres_type(data_type, char_len, num_prec, udt_name)

            column = ColumnAnnotation(
                name=col_name,
                description="",  # User will fill in
                data_type=col_type
            )

            # Check if this could be an enum (low cardinality)
            # Use TABLESAMPLE to avoid full table scans on large tables
            is_string_type = data_type in ['character varying', 'text', 'character']
            is_int_enum_candidate = (
                data_type in ['integer', 'smallint', 'bigint'] and
                self._is_enum_column_name(col_name)
            )

            if (is_string_type or is_int_enum_candidate) and row_estimate > 0:
                enum_values = self._sample_postgres_enum_values(
                    cursor, table_name, col_name, enum_threshold
                )

                if enum_values is not None and 0 < len(enum_values) <= enum_threshold:
                    column.data_type = "enum"
                    if sample_enums:
                        # Create placeholder mappings
                        column.enum_values = {
                            val: f"TODO: describe '{val}'"
                            for val in enum_values
                        }

            table.columns[col_name] = column

        return table

    def _sample_postgres_enum_values(self, cursor, table_name: str,
                                      col_name: str, threshold: int) -> list:
        """
        Sample distinct values from a PostgreSQL column using TABLESAMPLE.

        Uses progressive sampling to avoid full table scans on large tables.
        Returns None if the column has too many distinct values (not an enum).

        Args:
            cursor: Database cursor
            table_name: Name of the table
            col_name: Name of the column
            threshold: Maximum distinct values to consider as enum

        Returns:
            List of distinct values if <= threshold, else None
        """
        # Progressive sampling: start small, increase if needed
        for sample_pct in [0.1, 1, 10, 100]:
            if sample_pct < 100:
                # Use TABLESAMPLE for efficiency
                # Remove ORDER BY to avoid temp file creation
                cursor.execute(f"""
                    WITH sampled AS (
                        SELECT "{col_name}"
                        FROM "{table_name}" TABLESAMPLE SYSTEM({sample_pct})
                        LIMIT 50000
                    )
                    SELECT DISTINCT "{col_name}"
                    FROM sampled
                    WHERE "{col_name}" IS NOT NULL
                    LIMIT {threshold + 1}
                """)
            else:
                # Final attempt: full table but still with LIMIT
                # Remove ORDER BY to avoid temp file creation on large tables
                cursor.execute(f"""
                    SELECT DISTINCT "{col_name}"
                    FROM "{table_name}"
                    WHERE "{col_name}" IS NOT NULL
                    LIMIT {threshold + 1}
                """)

            values = [str(row[0]) for row in cursor.fetchall()]

            # If we found enough values or this was a significant sample
            if len(values) > threshold:
                # Too many distinct values - not an enum
                return None
            elif len(values) > 0 and (sample_pct >= 10 or len(values) <= threshold // 2):
                # Found some values and either:
                # - We've sampled enough (10%+), or
                # - We found very few values (likely an enum)
                return values

        return values if values else None

    def _normalize_postgres_type(self, data_type: str,
                                  char_len: Optional[int],
                                  num_prec: Optional[int],
                                  udt_name: Optional[str] = None) -> str:
        """Normalize PostgreSQL data type to simple type name.

        Args:
            data_type: The data_type from information_schema (e.g., 'integer', 'USER-DEFINED')
            char_len: Character maximum length for string types
            num_prec: Numeric precision for numeric types
            udt_name: The actual type name for USER-DEFINED types (e.g., 'ulid', 'geometry')

        Returns:
            Normalized type name
        """
        # For custom types (extensions like ULID, PostGIS, etc.), use the actual type name
        if data_type == 'USER-DEFINED' and udt_name:
            return udt_name

        type_map = {
            'integer': 'int',
            'bigint': 'bigint',
            'smallint': 'smallint',
            'numeric': 'decimal',
            'real': 'float',
            'double precision': 'double',
            'boolean': 'boolean',
            'character varying': 'string',
            'character': 'char',
            'text': 'text',
            'timestamp without time zone': 'timestamp',
            'timestamp with time zone': 'timestamptz',
            'date': 'date',
            'time without time zone': 'time',
            'time with time zone': 'timetz',
            'uuid': 'uuid',
            'json': 'json',
            'jsonb': 'jsonb',
            'bytea': 'binary',
            'inet': 'inet',
            'cidr': 'cidr',
            'macaddr': 'macaddr',
            'ARRAY': 'array'
        }

        return type_map.get(data_type, data_type)

    def _add_postgres_relationships(self, cursor, layer: SemanticLayer) -> None:
        """Add foreign key relationships to the semantic layer."""
        cursor.execute("""
            SELECT
                tc.table_name as source_table,
                kcu.column_name as source_column,
                ccu.table_name as target_table,
                ccu.column_name as target_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
        """)

        for row in cursor.fetchall():
            source_table, source_col, target_table, target_col = row

            if source_table in layer.tables:
                relationship = Relationship(
                    target_table=target_table,
                    join_pattern=f"{source_table}.{source_col} = {target_table}.{target_col}",
                    relationship_type="many_to_one",  # FK typically means many-to-one
                    description=""
                )
                layer.tables[source_table].relationships.append(relationship)

    def _add_postgres_extensions(self, cursor, layer: SemanticLayer) -> None:
        """
        Collect installed PostgreSQL extensions with descriptions and types.
        """
        from ..functions.postgres_metadata import fetch_postgres_extensions

        try:
            extensions = fetch_postgres_extensions(cursor)

            for ext_name, ext_version, description, types_str in extensions:
                types_list = [t.strip() for t in types_str.split(',') if t.strip()] if types_str else []
                layer.extensions[ext_name] = Extension(
                    name=ext_name,
                    version=ext_version or '',
                    description=description or '',
                    types_provided=types_list
                )

        except Exception:
            # Don't fail introspection if extension query fails
            pass

    def _add_postgres_custom_types(self, cursor, layer: SemanticLayer) -> None:
        """
        Collect custom types and domains defined in the database.
        """
        from ..functions.postgres_metadata import fetch_postgres_custom_types

        try:
            custom_types = fetch_postgres_custom_types(cursor)

            for type_name, type_code, type_category, base_type, enum_values in custom_types:
                enum_list = []
                if type_code == 'e' and enum_values:
                    enum_list = [v.strip() for v in enum_values.split(',')]

                description = ""
                extension = ""

                # Check if this type comes from a known extension
                if type_code == 'b':
                    description = "Custom type, compare with same type only"
                    # Try to match with known extension types
                    for ext_name, ext in layer.extensions.items():
                        if type_name in ext.types_provided:
                            extension = ext_name
                            break

                layer.custom_types[type_name] = CustomType(
                    name=type_name,
                    type_category=type_category,
                    base_type=base_type if type_code == 'd' else '',
                    enum_values=enum_list,
                    description=description,
                    extension=extension
                )

        except Exception:
            # Don't fail introspection if custom type query fails
            pass

    def _introspect_mysql(self, target_name: str,
                          enum_threshold: int,
                          sample_enums: bool) -> SemanticLayer:
        """Introspect MySQL database."""
        import pymysql

        params = self._get_connection_params()

        if not all([params['host'], params['user'], params['database'], params['password']]):
            raise ConnectionError("Missing database connection details")

        connection = pymysql.connect(
            host=params['host'],
            port=params['port'] or 3306,
            user=params['user'],
            password=params['password'],
            database=params['database'],
            connect_timeout=10
        )

        layer = SemanticLayer(target=target_name)

        try:
            with connection.cursor() as cursor:
                # Get all tables
                cursor.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """, (params['database'],))
                tables = [row[0] for row in cursor.fetchall()]

                for table_name in tables:
                    table_annotation = self._introspect_mysql_table(
                        cursor, table_name, params['database'],
                        enum_threshold, sample_enums
                    )
                    layer.tables[table_name] = table_annotation

                # Get foreign key relationships
                self._add_mysql_relationships(cursor, params['database'], layer)

        finally:
            connection.close()

        return layer

    def _introspect_mysql_table(self, cursor, table_name: str,
                                database: str, enum_threshold: int,
                                sample_enums: bool) -> TableAnnotation:
        """Introspect a single MySQL table."""
        # Get row estimate
        cursor.execute("""
            SELECT table_rows
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        """, (database, table_name))
        result = cursor.fetchone()
        row_estimate = result[0] if result else 0

        # Format row estimate
        if row_estimate >= 1_000_000:
            row_str = f"{row_estimate / 1_000_000:.1f}M"
        elif row_estimate >= 1_000:
            row_str = f"{row_estimate / 1_000:.1f}K"
        else:
            row_str = str(row_estimate)

        table = TableAnnotation(
            name=table_name,
            description="",
            row_estimate=row_str
        )

        # Get columns
        cursor.execute(f"DESCRIBE `{table_name}`")
        columns = cursor.fetchall()

        for col in columns:
            field, type_str, null, key, default, extra = col

            # Parse MySQL type
            col_type = self._normalize_mysql_type(type_str)

            column = ColumnAnnotation(
                name=field,
                description="",
                data_type=col_type
            )

            # Check for MySQL ENUM type
            if type_str.startswith('enum('):
                column.data_type = "enum"
                # Extract enum values from type definition
                enum_str = type_str[5:-1]  # Remove 'enum(' and ')'
                enum_values = [v.strip("'") for v in enum_str.split(',')]
                column.enum_values = {
                    val: f"TODO: describe '{val}'"
                    for val in enum_values
                }

            # Check for low cardinality strings or integer enum candidates
            # Use LIMIT-based sampling to avoid full table scans on large tables
            is_string_type = col_type in ['varchar', 'char', 'text']
            is_int_enum_candidate = (
                col_type in ['int', 'smallint', 'tinyint', 'mediumint', 'bigint'] and
                self._is_enum_column_name(field)
            )

            if (is_string_type or is_int_enum_candidate) and row_estimate > 0:
                enum_values = self._sample_mysql_enum_values(
                    cursor, table_name, field, enum_threshold
                )

                if enum_values is not None and 0 < len(enum_values) <= enum_threshold:
                    column.data_type = "enum"
                    if sample_enums:
                        column.enum_values = {
                            val: f"TODO: describe '{val}'"
                            for val in enum_values
                        }

            table.columns[field] = column

        return table

    def _sample_mysql_enum_values(self, cursor, table_name: str,
                                   col_name: str, threshold: int) -> list:
        """
        Sample distinct values from a MySQL column using LIMIT-based sampling.

        Uses progressive sampling to avoid full table scans on large tables.
        Returns None if the column has too many distinct values (not an enum).

        Args:
            cursor: Database cursor
            table_name: Name of the table
            col_name: Name of the column
            threshold: Maximum distinct values to consider as enum

        Returns:
            List of distinct values if <= threshold, else None
        """
        # Progressive sampling: start with small samples, increase if needed
        # MySQL doesn't have TABLESAMPLE, so we use LIMIT on subqueries
        for sample_limit in [10000, 50000, 100000]:
            # Remove ORDER BY to avoid temp file creation
            cursor.execute(f"""
                SELECT DISTINCT `{col_name}`
                FROM (
                    SELECT `{col_name}`
                    FROM `{table_name}`
                    LIMIT {sample_limit}
                ) subq
                WHERE `{col_name}` IS NOT NULL
                LIMIT {threshold + 1}
            """)

            values = [str(row[0]) for row in cursor.fetchall()]

            # If we found too many values, it's not an enum
            if len(values) > threshold:
                return None

            # If we found some values and sampled enough rows
            if len(values) > 0 and (sample_limit >= 50000 or len(values) <= threshold // 2):
                return values

        # Final attempt with no limit (but still bounded distinct)
        # Remove ORDER BY to avoid temp file creation
        cursor.execute(f"""
            SELECT DISTINCT `{col_name}`
            FROM `{table_name}`
            WHERE `{col_name}` IS NOT NULL
            LIMIT {threshold + 1}
        """)
        values = [str(row[0]) for row in cursor.fetchall()]

        if len(values) > threshold:
            return None

        return values if values else None

    def _normalize_mysql_type(self, type_str: str) -> str:
        """Normalize MySQL type string to simple type name."""
        # Remove size/precision info
        base_type = type_str.split('(')[0].lower()

        type_map = {
            'int': 'int',
            'bigint': 'bigint',
            'smallint': 'smallint',
            'tinyint': 'tinyint',
            'mediumint': 'mediumint',
            'decimal': 'decimal',
            'float': 'float',
            'double': 'double',
            'varchar': 'varchar',
            'char': 'char',
            'text': 'text',
            'mediumtext': 'text',
            'longtext': 'text',
            'datetime': 'datetime',
            'timestamp': 'timestamp',
            'date': 'date',
            'time': 'time',
            'year': 'year',
            'blob': 'binary',
            'mediumblob': 'binary',
            'longblob': 'binary',
            'json': 'json',
            'boolean': 'boolean',
            'bool': 'boolean'
        }

        return type_map.get(base_type, base_type)

    def _add_mysql_relationships(self, cursor, database: str,
                                  layer: SemanticLayer) -> None:
        """Add foreign key relationships to the semantic layer."""
        cursor.execute("""
            SELECT
                table_name as source_table,
                column_name as source_column,
                referenced_table_name as target_table,
                referenced_column_name as target_column
            FROM information_schema.key_column_usage
            WHERE table_schema = %s
              AND referenced_table_name IS NOT NULL
        """, (database,))

        for row in cursor.fetchall():
            source_table, source_col, target_table, target_col = row

            if source_table in layer.tables:
                relationship = Relationship(
                    target_table=target_table,
                    join_pattern=f"{source_table}.{source_col} = {target_table}.{target_col}",
                    relationship_type="many_to_one",
                    description=""
                )
                layer.tables[source_table].relationships.append(relationship)
