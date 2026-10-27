"""
Unit tests for lib/agent/runtime.py safety functions.

Tests SQL validation for read-only enforcement, LIMIT injection,
column denial, and table whitelisting.
"""

import pytest

from features.agent.runtime import (
    validate_read_only,
    inject_limit,
    validate_columns,
    validate_tables,
    AgentResponse,
)


class TestValidateReadOnly:
    """Tests for read-only SQL validation."""

    def test_select_is_allowed(self):
        """Test SELECT statements are allowed."""
        ok, msg = validate_read_only("SELECT * FROM users")
        assert ok is True
        assert msg == ""

    def test_select_with_where(self):
        """Test SELECT with WHERE clause."""
        ok, msg = validate_read_only("SELECT id, name FROM users WHERE active = true")
        assert ok is True

    def test_select_with_join(self):
        """Test SELECT with JOIN."""
        sql = """
        SELECT u.name, o.total
        FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE o.created_at > '2025-01-01'
        """
        ok, msg = validate_read_only(sql)
        assert ok is True

    def test_select_with_subquery(self):
        """Test SELECT with subquery."""
        sql = """
        SELECT * FROM users
        WHERE id IN (SELECT user_id FROM orders WHERE total > 100)
        """
        ok, msg = validate_read_only(sql)
        assert ok is True

    def test_with_cte_is_allowed(self):
        """Test WITH (CTE) statements are allowed."""
        sql = """
        WITH active_users AS (
            SELECT * FROM users WHERE active = true
        )
        SELECT * FROM active_users
        """
        ok, msg = validate_read_only(sql)
        assert ok is True

    def test_insert_is_rejected(self):
        """Test INSERT statements are rejected."""
        ok, msg = validate_read_only("INSERT INTO users (name) VALUES ('test')")
        assert ok is False
        assert "INSERT" in msg.upper()

    def test_update_is_rejected(self):
        """Test UPDATE statements are rejected."""
        ok, msg = validate_read_only("UPDATE users SET name = 'test' WHERE id = 1")
        assert ok is False
        assert "UPDATE" in msg.upper()

    def test_delete_is_rejected(self):
        """Test DELETE statements are rejected."""
        ok, msg = validate_read_only("DELETE FROM users WHERE id = 1")
        assert ok is False
        assert "DELETE" in msg.upper()

    def test_drop_is_rejected(self):
        """Test DROP statements are rejected."""
        ok, msg = validate_read_only("DROP TABLE users")
        assert ok is False
        assert "DROP" in msg.upper()

    def test_truncate_is_rejected(self):
        """Test TRUNCATE statements are rejected."""
        ok, msg = validate_read_only("TRUNCATE TABLE users")
        assert ok is False

    def test_create_is_rejected(self):
        """Test CREATE statements are rejected."""
        ok, msg = validate_read_only("CREATE TABLE test (id INT)")
        assert ok is False
        assert "CREATE" in msg.upper()

    def test_alter_is_rejected(self):
        """Test ALTER statements are rejected."""
        ok, msg = validate_read_only("ALTER TABLE users ADD COLUMN email VARCHAR(255)")
        assert ok is False
        assert "ALTER" in msg.upper()

    def test_unparseable_sql_is_rejected(self):
        """SQL that cannot be parsed cannot be shown to be read-only."""
        ok, msg = validate_read_only("SELECT FROM WHERE ((( ;; )")
        assert ok is False
        assert msg


class TestInjectLimit:
    """Tests for LIMIT injection."""

    def test_adds_limit_to_simple_query(self):
        """Test LIMIT is added to query without one."""
        sql = "SELECT * FROM users"
        result = inject_limit(sql, 100)

        assert "LIMIT" in result.upper()
        assert "100" in result

    def test_preserves_existing_limit(self):
        """Test existing LIMIT is preserved."""
        sql = "SELECT * FROM users LIMIT 50"
        result = inject_limit(sql, 100)

        # Should keep existing LIMIT 50, not add 100
        assert "50" in result

    def test_handles_complex_query(self):
        """Test LIMIT injection on complex query."""
        sql = """
        SELECT u.name, COUNT(o.id) as order_count
        FROM users u
        LEFT JOIN orders o ON u.id = o.user_id
        GROUP BY u.name
        ORDER BY order_count DESC
        """
        result = inject_limit(sql, 1000)

        assert "LIMIT" in result.upper()

    def test_returns_original_on_parse_error(self):
        """Test returns original SQL if parsing fails."""
        sql = "INVALID SQL SYNTAX HERE"
        result = inject_limit(sql, 100)

        # Should return original on parse failure
        assert result == sql


class TestValidateColumns:
    """Tests for column denial validation."""

    def test_no_denied_columns_passes(self):
        """Test query passes with no denied columns."""
        ok, msg = validate_columns("SELECT * FROM users", None)
        assert ok is True

    def test_empty_denied_list_passes(self):
        """Test query passes with empty denied list."""
        ok, msg = validate_columns("SELECT * FROM users", [])
        assert ok is True

    def test_exact_column_match_rejected(self):
        """Test exact column reference is rejected."""
        sql = "SELECT ssn FROM users"
        ok, msg = validate_columns(sql, ["ssn"])

        assert ok is False
        assert "ssn" in msg.lower()

    def test_table_qualified_column_rejected(self):
        """Test table.column reference is rejected."""
        sql = "SELECT users.ssn FROM users"
        ok, msg = validate_columns(sql, ["users.ssn"])

        assert ok is False
        assert "ssn" in msg.lower()

    def test_wildcard_pattern_rejected(self):
        """Test wildcard pattern denial works."""
        sql = "SELECT password_hash FROM users"
        ok, msg = validate_columns(sql, ["password*"])

        assert ok is False
        assert "password" in msg.lower()

    def test_allowed_columns_pass(self):
        """Test non-denied columns pass."""
        sql = "SELECT id, name, email FROM users"
        ok, msg = validate_columns(sql, ["ssn", "password"])

        assert ok is True

    def test_case_insensitive_match(self):
        """Test column denial is case-insensitive."""
        sql = "SELECT SSN FROM users"
        ok, msg = validate_columns(sql, ["ssn"])

        assert ok is False

    def test_multiple_denied_columns(self):
        """Test multiple denied columns."""
        sql = "SELECT id, ssn, password FROM users"
        denied = ["ssn", "password"]
        ok, msg = validate_columns(sql, denied)

        assert ok is False


class TestValidateTables:
    """Tests for table whitelist validation."""

    def test_no_whitelist_passes(self):
        """Test query passes with no whitelist."""
        ok, msg = validate_tables("SELECT * FROM users", None)
        assert ok is True

    def test_empty_whitelist_passes(self):
        """Test query passes with empty whitelist (means all allowed)."""
        ok, msg = validate_tables("SELECT * FROM users", [])
        assert ok is True

    def test_allowed_table_passes(self):
        """Test whitelisted table passes."""
        sql = "SELECT * FROM users"
        ok, msg = validate_tables(sql, ["users"])

        assert ok is True

    def test_not_allowed_table_rejected(self):
        """Test non-whitelisted table is rejected."""
        sql = "SELECT * FROM sensitive_data"
        ok, msg = validate_tables(sql, ["users", "orders"])

        assert ok is False
        assert "sensitive_data" in msg

    def test_join_with_allowed_tables(self):
        """Test JOIN with all allowed tables passes."""
        sql = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id"
        ok, msg = validate_tables(sql, ["users", "orders"])

        assert ok is True

    def test_join_with_disallowed_table(self):
        """Test JOIN with one disallowed table is rejected."""
        sql = "SELECT * FROM users u JOIN admin_logs a ON u.id = a.user_id"
        ok, msg = validate_tables(sql, ["users", "orders"])

        assert ok is False
        assert "admin_logs" in msg

    def test_case_insensitive_match(self):
        """Test table whitelist is case-insensitive."""
        sql = "SELECT * FROM USERS"
        ok, msg = validate_tables(sql, ["users"])

        assert ok is True


class TestAgentResponse:
    """Tests for AgentResponse dataclass."""

    def test_success_response(self):
        """Test successful response creation."""
        response = AgentResponse(
            success=True,
            sql="SELECT * FROM users",
            columns=["id", "name"],
            rows=[[1, "Alice"], [2, "Bob"]],
            row_count=2,
            execution_time_ms=50.5,
        )

        assert response.success is True
        assert response.sql == "SELECT * FROM users"
        assert response.row_count == 2

    def test_error_response(self):
        """Test error response creation."""
        response = AgentResponse(
            success=False,
            error="Access denied",
        )

        assert response.success is False
        assert response.error == "Access denied"

    def test_to_dict_success(self):
        """Test successful response serialization."""
        response = AgentResponse(
            success=True,
            sql="SELECT 1",
            columns=["col"],
            rows=[[1]],
            row_count=1,
        )
        result = response.to_dict()

        assert result["success"] is True
        assert result["sql"] == "SELECT 1"
        assert result["columns"] == ["col"]
        assert result["rows"] == [[1]]
        assert result["row_count"] == 1

    def test_to_dict_error(self):
        """Test error response serialization."""
        response = AgentResponse(
            success=False,
            error="Something went wrong",
        )
        result = response.to_dict()

        assert result["success"] is False
        assert result["error"] == "Something went wrong"
        assert "sql" not in result
        assert "rows" not in result

    def test_to_dict_omits_empty_fields(self):
        """Test serialization omits empty optional fields."""
        response = AgentResponse(success=True)
        result = response.to_dict()

        assert "sql" not in result
        assert "columns" not in result
        assert "rows" not in result
        assert "error" not in result


class TestReadOnlyCoversTheWholeStatement:
    """A write or side effect anywhere in the tree, or behind quoting, is refused."""

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
            "SELECT * INTO newt FROM t",
            "SELECT * FROM t FOR UPDATE",
            "SELECT nextval('s')",
            "SELECT set_config('statement_timeout', '0', false)",
            "SELECT pg_terminate_backend(1)",
            "SELECT dblink_exec('host=x', 'DROP TABLE t')",
            "SELECT lo_unlink(1)",
            "SELECT pg_sleep(100000)",
            'SELECT pg_catalog."pg_read_file"(\'/etc/passwd\')',
            "SELECT pg_catalog.\"setval\"('public.order_id_seq', 1, true)",
            "SELECT 1; DELETE FROM t",
            r"SELECT E'\''; DELETE FROM users; COMMIT; SELECT '1'",
        ],
    )
    def test_refused_on_postgresql(self, sql):
        ok, msg = validate_read_only(sql, "postgresql")

        assert ok is False
        assert msg

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT SLEEP(10)",
            "SELECT BENCHMARK(1000000, MD5('x'))",
            "SELECT GET_LOCK('x', 10)",
            "SELECT LOAD_FILE('/etc/passwd')",
            "SELECT * FROM t INTO OUTFILE '/tmp/x'",
        ],
    )
    def test_refused_on_mysql(self, sql):
        assert validate_read_only(sql, "mysql")[0] is False

    @pytest.mark.parametrize(
        "sql, dialect",
        [
            ("WITH r AS (SELECT * FROM orders) SELECT customer_id, count(*) FROM r GROUP BY 1",
             "postgresql"),
            ("SELECT id, rank() OVER (PARTITION BY region ORDER BY total DESC) FROM orders",
             "postgresql"),
            ("SELECT * FROM orders WHERE note = E'a;b' AND body = $$x; y$$", "postgresql"),
            ('SELECT "Order"."Id" FROM "Order" -- comment', "postgresql"),
            ("SELECT `order`.id FROM `order` WHERE name = 'O\\'Brien'", "mysql"),
        ],
    )
    def test_legitimate_reads_pass(self, sql, dialect):
        assert validate_read_only(sql, dialect) == (True, "")


TABLES = {"customers": ["id", "name", "ssn"], "orders": ["id", "customer_id", "total"]}


class TestRestrictionsResolveWhatIsRead:
    """Restrictions compare the columns and tables the database reads, not spellings."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM customers",
            "SELECT c.ssn AS value FROM customers c",
            "SELECT c.* FROM customers c",
            "SELECT row_to_json(c) FROM customers c",
            "SELECT to_json(c) FROM customers c",
            "SELECT row_to_json(c.*) FROM customers c",
            "SELECT c::text FROM customers c",
            "SELECT ROW(c.*) FROM customers c",
            "SELECT json_agg(c) FROM customers c",
            "SELECT hstore(c) FROM customers c",
            "SELECT customers FROM customers",
            "SELECT row_to_json(public.customers) FROM public.customers",
            "SELECT to_jsonb(customers.*) FROM customers",
            "SELECT format('%s', C) FROM customers c",
            "SELECT row_to_json(s) FROM (SELECT * FROM customers) s",
            "SELECT o.id FROM orders o WHERE EXISTS "
            "(SELECT 1 FROM customers c WHERE c::text LIKE '%1%')",
            "SELECT name FROM customers WHERE ssn LIKE '1%'",
            "SELECT (SELECT max(ssn) FROM customers) AS m",
            "WITH x AS (SELECT * FROM customers) SELECT ssn FROM x",
            "SELECT ssn FROM secret.customers",
        ],
    )
    @pytest.mark.parametrize("tables", [TABLES, None])
    def test_denied_column_is_refused_however_it_is_reached(self, sql, tables):
        ok, msg = validate_columns(
            sql, ["customers.ssn"], dialect="postgresql", tables=tables,
            default_schema="public",
        )

        assert ok is False
        assert msg

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM orders",
            "SELECT o.total, c.name FROM orders o JOIN customers c ON c.id = o.customer_id",
            "WITH x AS (SELECT * FROM customers) SELECT name FROM x",
            "SELECT name FROM (SELECT * FROM customers) s",
            "SELECT count(*) FROM customers",
            "SELECT name FROM customers c WHERE EXISTS "
            "(SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
        ],
    )
    def test_queries_that_never_read_the_column_pass_with_the_schema(self, sql):
        assert validate_columns(
            sql, ["customers.ssn"], dialect="postgresql", tables=TABLES,
            default_schema="public",
        ) == (True, "")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT row_to_json(o) FROM orders o",
            "SELECT row_to_json(s) FROM (SELECT name FROM customers) s",
            "SELECT status FROM status",
            "SELECT C.name FROM customers c",
        ],
    )
    def test_row_values_without_the_column_pass(self, sql):
        tables = {**TABLES, "status": ["id", "status"]}
        assert validate_columns(
            sql, ["customers.ssn"], dialect="postgresql", tables=tables, default_schema="public"
        ) == (True, "")

    def test_mysql_names_a_column_never_a_row(self):
        tables = {**TABLES, "status": ["id", "status"]}
        for sql, refused in [("SELECT status FROM status", False),
                             ("SELECT `c`.`name` FROM customers c", False),
                             ("SELECT c.ssn FROM customers c", True),
                             ("SELECT * FROM customers", True)]:
            ok, _ = validate_columns(sql, ["customers.ssn"], dialect="mysql", tables=tables,
                                     default_schema="app")
            assert ok is not refused, sql

    def test_star_without_the_schema_is_refused_with_a_reason(self):
        ok, msg = validate_columns("SELECT * FROM orders", ["customers.ssn"], dialect="postgresql")

        assert ok is False
        assert "list the columns" in msg

    def test_unparseable_sql_fails_closed(self):
        ok, msg = validate_columns("SELECT FROM WHERE (((", ["ssn"], dialect="postgresql")
        assert ok is False
        ok, msg = validate_tables("SELECT FROM WHERE (((", ["users"], dialect="postgresql")
        assert ok is False

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM customers WHERE id IN (SELECT id FROM admin)",
            "SELECT * FROM pg_catalog.pg_user",
            "SELECT * FROM generate_series(1, 3)",
        ],
    )
    def test_table_outside_the_allowlist_is_refused(self, sql):
        ok, _ = validate_tables(sql, ["customers"], dialect="postgresql", default_schema="public")

        assert ok is False

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT ssn FROM secret.customers",
            "SELECT * FROM customers c JOIN secret.customers s ON s.id = c.id",
        ],
    )
    def test_a_bare_entry_allows_the_table_in_any_schema(self, sql):
        """Only a schema-qualified entry pins the schema."""
        assert validate_tables(
            sql, ["customers"], dialect="postgresql", default_schema="public"
        ) == (True, "")
        ok, _ = validate_tables(
            sql, ["public.customers"], dialect="postgresql", default_schema="public")
        assert ok is False

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM customers",
            "SELECT * FROM public.customers",
            "WITH orders AS (SELECT id FROM customers) SELECT * FROM orders",
            "SELECT c.name FROM customers c WHERE EXISTS (SELECT 1 FROM customers c2)",
        ],
    )
    def test_allowed_table_passes_by_any_spelling(self, sql):
        assert validate_tables(
            sql, ["customers"], dialect="postgresql", default_schema="public"
        ) == (True, "")

    def test_schema_qualified_allowlist_entry(self):
        assert validate_tables("SELECT * FROM secret.customers", ["secret.customers"],
                               dialect="postgresql", default_schema="public")[0] is True
        assert validate_tables("SELECT * FROM customers", ["secret.customers"],
                               dialect="postgresql", default_schema="public")[0] is False
        assert validate_tables("SELECT * FROM public.customers", ["public.customers"],
                               dialect="postgresql", default_schema="public")[0] is True
        # Without the target the default schema is unknown, so this cannot be verified.
        ok, msg = validate_tables("SELECT * FROM customers", ["public.customers"],
                                  dialect="postgresql")
        assert ok is False and "could not be checked" in msg
