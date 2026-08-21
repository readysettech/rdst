"""
Tests for SQLGlot-based SQL normalization.

Tests the sql_normalizer module which provides robust AST-based
SQL parameterization using SQLGlot.
"""

import pytest
from shared.query_registry.sql_normalizer import (
    canonicalize_placeholder_style,
    normalize_and_extract,
    reconstruct_sql,
    get_placeholder_names,
    mask_string_literals,
    denormalize_for_readyset,
    parse_query_id_from_explain,
    parse_supported_from_explain,
)


class TestNormalizeAndExtract:
    """Tests for normalize_and_extract function."""

    def test_empty_query(self):
        """Empty query returns empty result."""
        normalized, params = normalize_and_extract("")
        assert normalized == ""
        assert params == {}

    def test_whitespace_only(self):
        """Whitespace-only query returns as-is."""
        normalized, params = normalize_and_extract("   ")
        assert normalized == "   "
        assert params == {}

    def test_basic_string_literal(self):
        """String literals are extracted with type info."""
        sql = "SELECT * FROM users WHERE name = 'John'"
        normalized, params = normalize_and_extract(sql)

        assert "'John'" not in normalized
        assert ":p" in normalized
        assert len(params) == 1
        # Find the param with value 'John'
        john_param = [p for p in params.values() if p['value'] == 'John'][0]
        assert john_param['type'] == 'string'

    def test_basic_numeric_literal(self):
        """Numeric literals are extracted with type info."""
        sql = "SELECT * FROM users WHERE id = 123"
        normalized, params = normalize_and_extract(sql)

        assert "123" not in normalized
        assert ":p" in normalized
        assert len(params) == 1
        # Find the param with value '123'
        num_param = [p for p in params.values() if p['value'] == '123'][0]
        assert num_param['type'] == 'number'

    def test_multiple_literals(self):
        """Multiple literals are extracted in order."""
        sql = "SELECT * FROM users WHERE status = 'active' AND age > 25 LIMIT 10"
        normalized, params = normalize_and_extract(sql)

        assert "'active'" not in normalized
        assert "25" not in normalized
        assert "10" not in normalized
        assert len(params) == 3

        # Check types
        types = {p['type'] for p in params.values()}
        assert 'string' in types
        assert 'number' in types

    def test_comment_with_literal_not_extracted(self):
        """Literals in comments should NOT be extracted (the key bug fix)."""
        sql = """-- Get 'active' customers
SELECT * FROM customers WHERE status = 'active' LIMIT 10"""
        normalized, params = normalize_and_extract(sql)

        # Should only extract 2 params (status value and LIMIT), not the comment
        assert len(params) == 2

        # The comment should be preserved (possibly converted to /* */ format)
        assert 'active' in normalized  # Comment content preserved

    def test_multiline_comment_with_literal(self):
        """Literals in multiline comments should NOT be extracted."""
        sql = """/* Filter for status = 'pending' orders */
SELECT * FROM orders WHERE status = 'shipped'"""
        normalized, params = normalize_and_extract(sql)

        # Should only extract 1 param (the actual WHERE value)
        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == 'shipped'

    def test_nested_subquery(self):
        """Literals in subqueries are extracted."""
        sql = """SELECT * FROM users
WHERE id IN (SELECT user_id FROM orders WHERE total > 100)
AND status = 'active'"""
        normalized, params = normalize_and_extract(sql)

        # Should extract both 100 and 'active'
        assert len(params) == 2
        values = {p['value'] for p in params.values()}
        assert '100' in values
        assert 'active' in values

    def test_decimal_numbers(self):
        """Decimal numbers are extracted correctly."""
        sql = "SELECT * FROM products WHERE price = 19.99"
        normalized, params = normalize_and_extract(sql)

        assert "19.99" not in normalized
        assert len(params) == 1

    def test_negative_numbers(self):
        """Negative numbers in expressions."""
        sql = "SELECT * FROM accounts WHERE balance > -100"
        normalized, params = normalize_and_extract(sql)

        # Note: negative sign may be part of expression, not literal
        assert len(params) >= 1

    def test_placeholder_naming(self):
        """Placeholders use :p1, :p2 naming convention."""
        sql = "SELECT * FROM t WHERE a = 1 AND b = 2 AND c = 3"
        normalized, params = normalize_and_extract(sql)

        # All params should have pN naming
        for name in params.keys():
            assert name.startswith('p')
            assert name[1:].isdigit()

    def test_preserves_query_structure(self):
        """SQL structure is preserved after normalization."""
        sql = "SELECT name, email FROM users WHERE status = 'active' ORDER BY name"
        normalized, params = normalize_and_extract(sql)

        assert "SELECT" in normalized.upper()
        assert "FROM" in normalized.upper()
        assert "WHERE" in normalized.upper()
        assert "ORDER BY" in normalized.upper()


class TestStructuralLiteralsSurvive:
    """Positional placeholders and ordinals are structure, not data."""

    def test_dollar_placeholders_survive_every_dialect(self):
        sql = (
            "SELECT id, name FROM users WHERE score > $1 "
            "ORDER BY created_at DESC LIMIT $2"
        )
        for dialect in (None, "postgres", "mysql"):
            normalized, params = normalize_and_extract(sql, dialect)
            assert params == {}
            assert "$1" in normalized
            assert "$2" in normalized
            assert "$:p" not in normalized

    def test_dollar_placeholder_mixed_with_literals(self):
        # Postgres parses $N as a Parameter wrapping the digit; only the
        # real literals become params.
        sql = "SELECT * FROM t WHERE a = $1 AND b = 'x' LIMIT 5"
        normalized, params = normalize_and_extract(sql, "postgres")
        assert "$1" in normalized
        values = {p["value"] for p in params.values()}
        assert values == {"x", "5"}

    def test_group_by_ordinal_stays_literal(self):
        sql = "SELECT a, COUNT(*) FROM t GROUP BY 1"
        for dialect in (None, "postgres"):
            normalized, params = normalize_and_extract(sql, dialect)
            assert "GROUP BY 1" in normalized
            assert params == {}

    def test_order_by_ordinal_stays_literal(self):
        sql = "SELECT a, b FROM t ORDER BY 2 DESC, 1"
        for dialect in (None, "postgres"):
            normalized, params = normalize_and_extract(sql, dialect)
            assert "ORDER BY 2 DESC, 1" in normalized
            assert params == {}

    def test_order_by_expression_literal_still_extracted(self):
        # A literal inside an ORDER BY expression is a value, not an ordinal.
        normalized, params = normalize_and_extract("SELECT a FROM t ORDER BY a + 1")
        assert "ORDER BY a + :p1" in normalized
        assert params["p1"]["value"] == "1"

    def test_window_order_by_constant_is_extracted(self):
        # ORDER BY inside OVER (...) orders rows by a constant, not a
        # select-list position; it is a value, not an ordinal.
        sql = "SELECT ROW_NUMBER() OVER (ORDER BY 1) FROM t"
        normalized, params = normalize_and_extract(sql)
        assert "ORDER BY :p1" in normalized
        assert params["p1"]["value"] == "1"

    def test_aggregate_order_by_constant_is_extracted(self):
        # ORDER BY inside an aggregate function argument list is likewise a
        # constant, not a select-list ordinal.
        sql = "SELECT ARRAY_AGG(x ORDER BY 1) FROM t"
        normalized, params = normalize_and_extract(sql)
        assert "ORDER BY :p1" in normalized
        assert params["p1"]["value"] == "1"

    def test_statement_level_group_by_ordinal_still_stays_literal(self):
        # Regression: plain GROUP BY 1 at the statement level is still an
        # ordinal and must not be affected by the window/aggregate fix.
        sql = "SELECT a, COUNT(*) FROM t GROUP BY 1"
        normalized, params = normalize_and_extract(sql)
        assert "GROUP BY 1" in normalized
        assert params == {}

    def test_statement_level_order_by_ordinal_still_stays_literal(self):
        # Regression: plain ORDER BY 2 at the statement level is still an
        # ordinal and must not be affected by the window/aggregate fix.
        sql = "SELECT a, b FROM t ORDER BY 2"
        normalized, params = normalize_and_extract(sql)
        assert "ORDER BY 2" in normalized
        assert params == {}

    def test_union_order_by_ordinal_still_stays_literal(self):
        sql = "SELECT a FROM t1 UNION SELECT b FROM t2 ORDER BY 1"
        normalized, params = normalize_and_extract(sql)
        assert "ORDER BY 1" in normalized
        assert params == {}

    def test_ordinal_alongside_extracted_values(self):
        sql = (
            "SELECT DATE_TRUNC($1, created_at) AS day, COUNT(*) FROM events "
            "WHERE type = 'click' GROUP BY 1 ORDER BY 1 DESC LIMIT 50"
        )
        normalized, params = normalize_and_extract(sql, "postgres")
        assert "$1" in normalized
        assert "GROUP BY 1" in normalized
        assert "ORDER BY 1 DESC" in normalized
        values = {p["value"] for p in params.values()}
        assert values == {"click", "50"}


class TestReconstructSql:
    """Tests for reconstruct_sql function."""

    def test_basic_reconstruction(self):
        """Basic reconstruction replaces placeholders with values."""
        normalized = "SELECT * FROM users WHERE id = :p1"
        params = {'p1': {'value': 123, 'type': 'number'}}

        result = reconstruct_sql(normalized, params)
        assert "123" in result
        assert ":p1" not in result

    def test_string_reconstruction(self):
        """String values are properly quoted."""
        normalized = "SELECT * FROM users WHERE name = :p1"
        params = {'p1': {'value': 'John', 'type': 'string'}}

        result = reconstruct_sql(normalized, params)
        assert "'John'" in result

    def test_multiple_params(self):
        """Multiple parameters are reconstructed."""
        normalized = "SELECT * FROM users WHERE status = :p1 AND age > :p2 LIMIT :p3"
        params = {
            'p1': {'value': 'active', 'type': 'string'},
            'p2': {'value': 25, 'type': 'number'},
            'p3': {'value': 10, 'type': 'number'}
        }

        result = reconstruct_sql(normalized, params)
        assert "'active'" in result
        assert "25" in result
        assert "10" in result

    def test_empty_params(self):
        """Empty params returns SQL as-is."""
        sql = "SELECT * FROM users"
        result = reconstruct_sql(sql, {})
        assert result == sql

    def test_roundtrip(self):
        """Normalize then reconstruct produces equivalent SQL."""
        original = "SELECT * FROM users WHERE status = 'active' AND age > 25 LIMIT 10"
        normalized, params = normalize_and_extract(original)
        reconstructed = reconstruct_sql(normalized, params)

        # Should be semantically equivalent (may differ in formatting)
        assert "'active'" in reconstructed
        assert "25" in reconstructed
        assert "10" in reconstructed

    def test_missing_param_not_replaced(self):
        """Missing parameters are not replaced."""
        normalized = "SELECT * FROM users WHERE id = :p1 AND name = :p2"
        params = {'p1': {'value': 123, 'type': 'number'}}

        result = reconstruct_sql(normalized, params)
        assert "123" in result
        assert ":p2" in result  # Not replaced


class TestGetPlaceholderNames:
    """Tests for get_placeholder_names function."""

    def test_single_placeholder(self):
        """Single placeholder is detected."""
        sql = "SELECT * FROM users WHERE id = :p1"
        names = get_placeholder_names(sql)
        assert names == {'p1'}

    def test_multiple_placeholders(self):
        """Multiple placeholders are detected."""
        sql = "SELECT * FROM users WHERE status = :p1 AND age > :p2 LIMIT :p3"
        names = get_placeholder_names(sql)
        assert names == {'p1', 'p2', 'p3'}

    def test_no_placeholders(self):
        """No placeholders returns empty set."""
        sql = "SELECT * FROM users"
        names = get_placeholder_names(sql)
        assert names == set()

    def test_empty_query(self):
        """Empty query returns empty set."""
        names = get_placeholder_names("")
        assert names == set()

    def test_duplicate_placeholder(self):
        """Same placeholder used twice is counted once."""
        sql = "SELECT * FROM users WHERE id = :p1 OR parent_id = :p1"
        names = get_placeholder_names(sql)
        assert names == {'p1'}


class TestPositionalPlaceholders:
    """`$N` slots of engine-normalized texts name the same parameters as `:pN`."""

    def test_dollar_slots_are_named_positionally(self):
        sql = "SELECT * FROM users WHERE id = $1 LIMIT $2"
        assert get_placeholder_names(sql) == {"p1", "p2"}

    def test_mixed_styles_are_both_named(self):
        sql = "SELECT * FROM users WHERE name = :p1 AND id = $2"
        assert get_placeholder_names(sql) == {"p1", "p2"}

    def test_dollar_quoted_string_is_not_a_placeholder(self):
        assert get_placeholder_names("SELECT $$100$$ FROM users") == set()
        assert get_placeholder_names("SELECT $tag$x$1$tag$ FROM users") == set()

    def test_dollar_quoted_string_keeps_real_slots(self):
        sql = "SELECT $$100$$ FROM users WHERE id = $1"
        assert get_placeholder_names(sql) == {"p1"}

    def test_slot_inside_string_literal_is_not_a_placeholder(self):
        assert get_placeholder_names("SELECT * FROM t WHERE s = '$1'") == set()

    def test_reconstruct_substitutes_dollar_slots(self):
        sql = "SELECT * FROM users WHERE id = $1 AND name = $2"
        params = {
            "p1": {"value": 7, "type": "number"},
            "p2": {"value": "ana", "type": "string"},
        }

        result = reconstruct_sql(sql, params, dialect="postgres")

        assert "$1" not in result and "$2" not in result
        assert "7" in result
        assert "'ana'" in result

    def test_reconstruct_substitutes_mixed_styles(self):
        sql = "SELECT * FROM users WHERE name = :p1 AND id = $2"
        params = {
            "p1": {"value": "ana", "type": "string"},
            "p2": {"value": 7, "type": "number"},
        }

        result = reconstruct_sql(sql, params, dialect="postgres")

        assert "'ana'" in result
        assert "7" in result
        assert "$2" not in result and ":p1" not in result

    def test_reconstruct_leaves_dollar_quoted_text_alone(self):
        sql = "SELECT $$100$$ FROM users WHERE id = $1"
        params = {"p1": {"value": 7, "type": "number"}}

        result = reconstruct_sql(sql, params)

        assert "$$100$$" in result
        assert result.endswith("= 7")

    def test_reconstruct_keeps_unknown_slot(self):
        sql = "SELECT * FROM users WHERE id = $1 AND org = $2"
        params = {"p1": {"value": 7, "type": "number"}}

        result = reconstruct_sql(sql, params, dialect="postgres")

        assert "$2" in result

    def test_reconstruct_quotes_a_value_that_spells_a_slot(self):
        sql = "SELECT * FROM users WHERE name = $1"
        params = {"p1": {"value": "$1", "type": "string"}}

        assert reconstruct_sql(sql, params, dialect="postgres").endswith("= '$1'")

    def test_mask_string_literals_preserves_offsets(self):
        sql = "SELECT $$a$$, 'b' FROM t WHERE id = $1"
        masked = mask_string_literals(sql)

        assert len(masked) == len(sql)
        assert masked.endswith("= $1")
        assert "$$a$$" not in masked and "'b'" not in masked


class TestEdgeCases:
    """Edge case tests."""

    def test_escaped_quotes_in_string(self):
        """Escaped quotes in strings are handled."""
        sql = "SELECT * FROM users WHERE name = 'O''Brien'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        # Value should include the escaped quote
        param = list(params.values())[0]
        assert "Brien" in param['value']

    def test_unicode_in_strings(self):
        """Unicode characters in strings are preserved."""
        sql = "SELECT * FROM users WHERE name = 'José'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == 'José'

    def test_very_long_string(self):
        """Very long strings are handled."""
        long_value = 'x' * 1000
        sql = f"SELECT * FROM logs WHERE message = '{long_value}'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == long_value

    def test_null_handling(self):
        """NULL is not treated as a literal."""
        sql = "SELECT * FROM users WHERE deleted_at IS NULL"
        normalized, params = normalize_and_extract(sql)

        # NULL should not be extracted as a parameter
        assert "NULL" in normalized.upper()

    def test_boolean_values(self):
        """Boolean values in SQL."""
        sql = "SELECT * FROM users WHERE active = true"
        normalized, params = normalize_and_extract(sql)

        # Behavior depends on SQLGlot - just ensure no crash
        assert "SELECT" in normalized.upper()

    def test_date_literal(self):
        """Date literals are extracted."""
        sql = "SELECT * FROM orders WHERE created_at > '2024-01-01'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == '2024-01-01'
        assert param['type'] == 'string'

    def test_complex_expression(self):
        """Complex expressions with multiple literals."""
        sql = """
        SELECT u.name, COUNT(*) as order_count
        FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE o.status = 'completed'
          AND o.total > 100
          AND o.created_at > '2024-01-01'
        GROUP BY u.name
        HAVING COUNT(*) > 5
        ORDER BY order_count DESC
        LIMIT 10
        """
        normalized, params = normalize_and_extract(sql)

        # Should extract: 'completed', 100, '2024-01-01', 5, 10
        assert len(params) == 5


class TestFallbackBehavior:
    """Tests for fallback behavior when SQLGlot fails."""

    def test_invalid_sql_uses_fallback(self):
        """Invalid SQL falls back to regex normalization."""
        # This is intentionally malformed SQL
        sql = "SELECTT * FROMM users WHERE"
        normalized, params = normalize_and_extract(sql)

        # Should not crash, should return something
        assert normalized is not None

    def test_dialect_hint(self):
        """Dialect hint is used for parsing."""
        sql = "SELECT * FROM users WHERE id = 1"
        # Test with explicit dialect
        normalized, params = normalize_and_extract(sql, dialect='postgres')
        assert len(params) == 1

    def test_fallback_preserves_dollar_placeholders(self):
        """Dialect-less sqlglot cannot parse TABLESAMPLE, so this routes
        through the regex fallback; $N slots must survive verbatim."""
        sql = (
            "SELECT id, name FROM users TABLESAMPLE SYSTEM($1) "
            "WHERE score > 10 LIMIT $2"
        )
        normalized, params = normalize_and_extract(sql)
        assert "SYSTEM($1)" in normalized
        assert "LIMIT $2" in normalized
        assert "$:p" not in normalized
        assert [p["value"] for p in params.values()] == ["10"]

    def test_fallback_multi_digit_slot_index_preserved(self):
        from shared.query_registry.sql_normalizer import _fallback_normalize

        normalized, params = _fallback_normalize(
            "SELECT a FROM t WHERE b = $12 AND c = 34"
        )
        assert normalized == "SELECT a FROM t WHERE b = $12 AND c = :p1"
        assert params == {"p1": {"value": "34", "type": "number"}}

    def test_fallback_extracts_digits_inside_dollar_quoted_string(self):
        from shared.query_registry.sql_normalizer import _fallback_normalize

        normalized, params = _fallback_normalize(
            "SELECT $$100$$ FROM t TABLESAMPLE SYSTEM($1)"
        )
        assert normalized == "SELECT $$:p1$$ FROM t TABLESAMPLE SYSTEM($1)"
        assert params == {"p1": {"value": "100", "type": "number"}}

    def test_fallback_positional_placeholders_still_preserved(self):
        from shared.query_registry.sql_normalizer import _fallback_normalize

        normalized, params = _fallback_normalize(
            "SELECT id FROM users WHERE score > $1 LIMIT $2"
        )
        assert normalized == "SELECT id FROM users WHERE score > $1 LIMIT $2"
        assert params == {}

    def test_fallback_placeholder_and_literal_in_same_statement(self):
        from shared.query_registry.sql_normalizer import _fallback_normalize

        normalized, params = _fallback_normalize(
            "SELECT a FROM t WHERE b = $1 AND c = 34"
        )
        assert normalized == "SELECT a FROM t WHERE b = $1 AND c = :p1"
        assert params == {"p1": {"value": "34", "type": "number"}}

    def test_fallback_doubles_embedded_quotes(self):
        """The regex fallback must not let a value terminate its own literal."""
        from shared.query_registry.sql_normalizer import _fallback_reconstruct

        result = _fallback_reconstruct(
            "SELECT * FROM users WHERE name = :p1",
            {'p1': {'value': "O'Brien'; DROP TABLE users --", 'type': 'string'}},
        )

        assert result == (
            "SELECT * FROM users WHERE name = "
            "'O''Brien''; DROP TABLE users --'"
        )


# =============================================================================
# denormalize_for_readyset — engine-aware conversion of :pN placeholders
# =============================================================================

class TestCanonicalizePlaceholderStyle:
    """Placeholder-style canonicalization used for hash derivation."""

    def test_empty_input_passes_through(self):
        assert canonicalize_placeholder_style("") == ""

    def test_dollar_placeholders_map_to_pn(self):
        # pg_stat_statements numbers $N textually, so indices are preserved.
        assert (
            canonicalize_placeholder_style(
                "SELECT * FROM t WHERE a = $1 AND b = $2"
            )
            == "SELECT * FROM t WHERE a = :p1 AND b = :p2"
        )

    def test_dollar_index_preserved_through_cast(self):
        assert (
            canonicalize_placeholder_style(
                "SELECT * FROM orders WHERE ids = ANY($1::bigint[]) LIMIT $2"
            )
            == "SELECT * FROM orders WHERE ids = ANY(:p1::bigint[]) LIMIT :p2"
        )

    def test_anonymous_qmarks_number_by_position(self):
        assert (
            canonicalize_placeholder_style("SELECT a FROM t WHERE b = ? LIMIT ?")
            == "SELECT a FROM t WHERE b = :p1 LIMIT :p2"
        )

    def test_traversal_numbered_pn_renumbers_textually(self):
        # RDST's literal extraction names :pN in AST-traversal order; the
        # canonical form uses textual order so every style converges.
        assert (
            canonicalize_placeholder_style(
                "SELECT a FROM t WHERE b = :p2 LIMIT :p1"
            )
            == "SELECT a FROM t WHERE b = :p1 LIMIT :p2"
        )

    def test_textually_ordered_pn_is_a_fixed_point(self):
        sql = "SELECT a FROM t WHERE b = :p1 AND c IN (:p2, :p3)"
        assert canonicalize_placeholder_style(sql) == sql

    def test_legacy_fused_dollar_pn_folds(self):
        # Stored texts from builds that lifted the digit out of a $N
        # parameter carry `$:pN`; they must converge with the clean form.
        assert (
            canonicalize_placeholder_style(
                "SELECT * FROM t WHERE score > $:p2 LIMIT $:p1"
            )
            == "SELECT * FROM t WHERE score > :p1 LIMIT :p2"
        )

    def test_fused_and_clean_spellings_converge(self):
        clean = canonicalize_placeholder_style(
            "SELECT * FROM t WHERE score > $1 LIMIT $2"
        )
        fused = canonicalize_placeholder_style(
            "SELECT * FROM t WHERE score > $:p2 LIMIT $:p1"
        )
        assert clean == fused == "SELECT * FROM t WHERE score > :p1 LIMIT :p2"

    def test_postgres_json_operators_untouched(self):
        for sql in (
            "SELECT * FROM t WHERE tags ?| ARRAY[:p1, :p2]",
            "SELECT * FROM t WHERE tags ?& ARRAY[:p1]",
            "SELECT * FROM t WHERE data ?? :p1",
            "SELECT * FROM t WHERE data @? :p1",
        ):
            assert canonicalize_placeholder_style(sql) == sql

    def test_question_mark_inside_string_never_matches(self):
        # Hash derivation runs this on normalize()d SQL, where string
        # literals are already :pN placeholders; a literal containing '?'
        # therefore contributes exactly one canonical slot.
        normalized, _ = normalize_and_extract(
            "SELECT * FROM t WHERE name = 'who?'"
        )
        assert canonicalize_placeholder_style(normalized) == (
            "SELECT * FROM t WHERE name = :p1"
        )


class TestDenormalizeForReadyset:
    """ReadySet uses engine-specific placeholders ($N for Postgres, ? for MySQL).

    Empirically verified against readysettech/readyset:latest on Postgres mode
    that `IN (?)` produces a syntax error but `IN ($1)`, `IN ($1, $2, $3)`, and
    `IN ('a','b','c')` all canonicalize to the same cache. So we don't collapse
    IN — we just match placeholder syntax.
    """

    def test_postgres_simple_placeholder(self):
        assert denormalize_for_readyset("SELECT * FROM users WHERE id = :p1", engine="postgresql") == \
            "SELECT * FROM users WHERE id = $1"

    def test_postgres_in_list_keeps_indexed_placeholders(self):
        assert denormalize_for_readyset("SELECT * FROM foo WHERE a IN (:p1, :p2, :p3)", engine="postgresql") == \
            "SELECT * FROM foo WHERE a IN ($1, $2, $3)"

    def test_postgres_not_in_list(self):
        assert denormalize_for_readyset("SELECT * FROM foo WHERE a NOT IN (:p1, :p2, :p3)", engine="postgresql") == \
            "SELECT * FROM foo WHERE a NOT IN ($1, $2, $3)"

    def test_postgres_literal_in_list_passthrough(self):
        sql = "SELECT * FROM foo WHERE a IN (1, 2, 3)"
        assert denormalize_for_readyset(sql, engine="postgresql") == sql

    def test_postgres_mixed_predicate(self):
        assert denormalize_for_readyset(
            "SELECT * FROM foo WHERE x = :p1 AND y IN (:p2, :p3, :p4)", engine="postgresql"
        ) == "SELECT * FROM foo WHERE x = $1 AND y IN ($2, $3, $4)"

    def test_postgres_subquery_in_clause_unchanged(self):
        sql = "SELECT * FROM foo WHERE a IN (SELECT id FROM bar)"
        assert denormalize_for_readyset(sql, engine="postgresql") == sql

    def test_postgres_subquery_inner_placeholder_converted(self):
        sql = "SELECT * FROM foo WHERE a IN (SELECT id FROM bar WHERE x = :p1)"
        assert denormalize_for_readyset(sql, engine="postgresql") == \
            "SELECT * FROM foo WHERE a IN (SELECT id FROM bar WHERE x = $1)"

    def test_postgres_high_numbered_placeholders(self):
        sql = "SELECT * FROM foo WHERE a = :p10 AND b = :p100"
        assert denormalize_for_readyset(sql, engine="postgresql") == \
            "SELECT * FROM foo WHERE a = $10 AND b = $100"

    def test_mysql_simple_placeholder(self):
        assert denormalize_for_readyset("SELECT * FROM users WHERE id = :p1", engine="mysql") == \
            "SELECT * FROM users WHERE id = ?"

    def test_mysql_in_list_collapses(self):
        assert denormalize_for_readyset("SELECT * FROM foo WHERE a IN (:p1, :p2, :p3)", engine="mysql") == \
            "SELECT * FROM foo WHERE a IN (?)"

    def test_mysql_mixed_predicate(self):
        assert denormalize_for_readyset(
            "SELECT * FROM foo WHERE x = :p1 AND y IN (:p2, :p3, :p4)", engine="mysql"
        ) == "SELECT * FROM foo WHERE x = ? AND y IN (?)"

    def test_default_engine_is_postgres(self):
        assert denormalize_for_readyset("WHERE id = :p1") == "WHERE id = $1"

    def test_empty_input(self):
        assert denormalize_for_readyset("", engine="postgresql") == ""
        assert denormalize_for_readyset("", engine="mysql") == ""

    def test_none_input(self):
        assert denormalize_for_readyset(None, engine="postgresql") is None

    def test_no_placeholders_passthrough(self):
        sql = "SELECT * FROM users LIMIT 10"
        assert denormalize_for_readyset(sql, engine="postgresql") == sql
        assert denormalize_for_readyset(sql, engine="mysql") == sql

    def test_unknown_engine_passthrough(self):
        sql = "SELECT * FROM users WHERE id = :p1"
        assert denormalize_for_readyset(sql, engine="quantum") == sql


# =============================================================================
# parse_query_id_from_explain / parse_supported_from_explain
# =============================================================================

class TestParseQueryIdFromExplain:

    def test_pipe_delimited_output(self):
        output = (
            "query id    | q_13b0714e3f57aa57\n"
            "query       | SELECT * FROM users WHERE id = ?\n"
            "readyset supported | yes"
        )
        assert parse_query_id_from_explain(output) == "q_13b0714e3f57aa57"

    def test_tab_delimited_output(self):
        output = "query id\tq_abc123def456789a\nquery\tSELECT 1\nsupported\tyes"
        assert parse_query_id_from_explain(output) == "q_abc123def456789a"

    def test_short_hex_id(self):
        output = "query id | q_abc12345"
        assert parse_query_id_from_explain(output) == "q_abc12345"

    def test_uppercase_hex(self):
        output = "query id | q_ABCDEF1234567890"
        assert parse_query_id_from_explain(output) == "q_ABCDEF1234567890"

    def test_no_match_returns_none(self):
        assert parse_query_id_from_explain("just some text without query id") is None

    def test_empty_string_returns_none(self):
        assert parse_query_id_from_explain("") is None

    def test_none_input_returns_none(self):
        assert parse_query_id_from_explain(None) is None

    def test_q_prefix_in_other_word_doesnt_match(self):
        assert parse_query_id_from_explain("query: select 1") is None


class TestParseSupportedFromExplain:

    def test_yes(self):
        output = "query id | q_abc12345\nreadyset supported | yes"
        assert parse_supported_from_explain(output) == "yes"

    def test_pending(self):
        output = "readyset supported | pending"
        assert parse_supported_from_explain(output) == "pending"

    def test_unsupported_with_reason(self):
        output = "readyset supported | unsupported: aggregation not supported"
        assert parse_supported_from_explain(output) == "unsupported: aggregation not supported"

    def test_tab_delimited(self):
        output = "readyset supported\tyes"
        assert parse_supported_from_explain(output) == "yes"

    def test_empty_returns_empty(self):
        assert parse_supported_from_explain("") == ""

    def test_none_returns_empty(self):
        assert parse_supported_from_explain(None) == ""

    def test_no_supported_line(self):
        assert parse_supported_from_explain("just q_abc123") == ""


class TestReferencesUserRelations:
    """Tests for the system-vs-user relation classifier."""

    def test_bare_pg_prefixed_names_are_system(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert references_user_relations("SELECT * FROM pg_stat_user_indexes") is False
        assert references_user_relations("SELECT * FROM pg_tables") is False

    def test_qualified_system_schemas_are_system(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert (
            references_user_relations("SELECT * FROM pg_catalog.pg_class") is False
        )
        assert (
            references_user_relations("SELECT * FROM information_schema.tables")
            is False
        )
        assert (
            references_user_relations(
                "SELECT * FROM performance_schema.events_statements_summary_by_digest",
                dialect="mysql",
            )
            is False
        )
        assert (
            references_user_relations("SELECT * FROM mysql.user", dialect="mysql")
            is False
        )

    def test_user_tables_are_user(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert references_user_relations("SELECT * FROM users WHERE id = 1") is True
        assert (
            references_user_relations("SELECT * FROM public.orders o JOIN items i ON i.order_id = o.id")
            is True
        )

    def test_mixed_user_and_system_is_user(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert (
            references_user_relations(
                "SELECT u.* FROM users u JOIN pg_catalog.pg_class c ON true"
            )
            is True
        )

    def test_relation_free_statements_are_not_user(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert references_user_relations("SELECT 1") is False
        assert references_user_relations("SELECT now()") is False

    def test_cte_alias_is_not_a_relation(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert (
            references_user_relations(
                "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent"
            )
            is True
        )
        assert (
            references_user_relations(
                "WITH recent AS (SELECT * FROM pg_stat_activity) SELECT * FROM recent"
            )
            is False
        )

    def test_unparseable_sql_is_kept_as_user(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert references_user_relations("THIS IS NOT (((SQL") is True

    def test_rdst_self_marker_is_never_user(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert (
            references_user_relations(
                "/*rdst:observe*/ SELECT userid, dbid, queryid, toplevel, query "
                "FROM pg_stat_statements WHERE queryid = ANY($1::bigint[]) AND dbid = $2"
            )
            is False
        )
        assert (
            references_user_relations("/*rdst:analyze*/ SELECT * FROM users")
            is False
        )

    def test_utility_statements_are_not_user(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert (
            references_user_relations("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            is False
        )
        assert references_user_relations("SET work_mem = '64MB'") is False
        assert references_user_relations("COMMIT") is False
        assert references_user_relations("SHOW server_version") is False

    def test_parse_failure_falls_back_to_relation_tokens(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        # Unmarked variant of RDST's own text fetch: the array cast defeats
        # the AST parser, the token fallback still sees only a system table.
        assert (
            references_user_relations(
                "SELECT userid, dbid, queryid, toplevel, query "
                "FROM pg_stat_statements WHERE queryid = ANY($1::bigint[]) AND dbid = $2"
            )
            is False
        )
        # pg_dump-style catalog SQL: all captured tokens are system relations.
        assert (
            references_user_relations(
                "SELECT t.tableoid, t.oid FROM pg_catalog.pg_index i "
                "JOIN pg_catalog.pg_class t ON (t.oid = i.indexrelid) "
                "WHERE i.indrelid = $1::pg_catalog.oid[]"
            )
            is False
        )

    def test_parse_failure_with_user_relation_token_is_user(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert (
            references_user_relations(
                "SELECT * FROM orders WHERE ids = ANY($1::bigint[])"
            )
            is True
        )

    def test_leading_comment_is_looked_through(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        assert (
            references_user_relations("/* app comment */ SELECT * FROM users")
            is True
        )
        assert (
            references_user_relations("/* app comment */ SELECT * FROM pg_tables")
            is False
        )

    def test_mysql_describe_admits_as_user_but_marker_excludes_it(self):
        from shared.query_registry.sql_normalizer import references_user_relations

        # Unmarked, MySQL's admission classifier fails open on DESCRIBE (the
        # introspector's own bug): it looks like it touches a user table.
        assert (
            references_user_relations("DESCRIBE `orders`", dialect="mysql") is True
        )
        # The introspector prefixes its self-marker on this statement, which
        # keeps it out of registry admission like every other profiling probe.
        assert (
            references_user_relations(
                "/*rdst:profile*/ DESCRIBE `orders`", dialect="mysql"
            )
            is False
        )
