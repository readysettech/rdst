from types import SimpleNamespace

from features.ask.source_literal_normalization import (
    normalize_context,
    normalize_explicit_year_span_sql,
)


def normalize(question, sql, context="", dialect="mysql"):
    return normalize_explicit_year_span_sql(
        question=question,
        provided_context=context,
        sql=sql,
        dialect=dialect,
    )


def test_expands_shortened_filter_span_from_exact_question_text():
    sql, diagnostics = normalize(
        "Show enrollment for the 2014-2015 academic year.",
        "SELECT enrollment FROM schools WHERE academic_year = '2014-15'",
    )

    assert "academic_year = '2014-2015'" in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["changes"] == [{"from": "2014-15", "to": "2014-2015"}]


def test_accepts_authoritative_context_as_source():
    sql, diagnostics = normalize(
        "Show enrollment for that academic year.",
        "SELECT enrollment FROM schools WHERE academic_year = '2014-15'",
        context="The requested academic year is 2014-2015.",
    )

    assert "'2014-2015'" in sql
    assert diagnostics["changed_literals"] == 1


def test_does_not_rewrite_full_date_literal():
    original = "SELECT * FROM visits WHERE visit_date > '2012-01-01'"
    sql, diagnostics = normalize("Show visits after 2012-01-01.", original)

    assert sql == original
    assert diagnostics["reason"] == "no-explicit-full-year-span"


def test_does_not_infer_full_span_when_source_omits_it():
    original = "SELECT * FROM schools WHERE academic_year = '2014-15'"
    sql, diagnostics = normalize("Show the 2014-15 academic year.", original)

    assert sql == original
    assert diagnostics["reason"] == "no-explicit-full-year-span"


def test_ambiguous_source_spans_fail_closed():
    original = "SELECT * FROM schools WHERE academic_year = '2014-15'"
    sql, diagnostics = normalize(
        "Compare 2014-2015 with 2014/2015.",
        original,
    )

    assert sql == original
    assert diagnostics["reason"] == "no-lossy-year-span-filter"


def test_non_filter_literal_is_unchanged():
    original = "SELECT '2014-15' AS label FROM schools"
    sql, diagnostics = normalize("Use the label 2014-2015.", original)

    assert sql == original
    assert diagnostics["reason"] == "no-lossy-year-span-filter"


def test_postgres_and_context_wrapper():
    ctx = SimpleNamespace(
        question="Show the 2014-2015 academic year.",
        refined_question=None,
        provided_context="",
        db_type="postgresql",
        sql="SELECT enrollment FROM schools WHERE academic_year = '2014-15'",
        generated_sql="old",
        explicit_year_span_normalization={},
    )

    result = normalize_context(ctx)

    assert result is ctx
    assert "2014-2015" in result.sql
    assert result.generated_sql == result.sql
    assert result.explicit_year_span_normalization["status"] == "normalized"


def test_translates_exact_sqlite_year_extraction_for_mysql():
    original = (
        "SELECT t.team_long_name FROM Team t JOIN Team_Attributes ta "
        "ON t.team_api_id = ta.team_api_id "
        "WHERE strftime('%Y', ta.date) = '2012' "
        "AND ta.score > (SELECT AVG(score) FROM Team_Attributes "
        "WHERE strftime('%Y', date) = '2012')"
    )

    sql, diagnostics = normalize("Show above-average teams in 2012.", original)

    assert sql.count("YEAR(") == 2
    assert "STRFTIME" not in sql
    assert diagnostics["status"] == "normalized"
    assert diagnostics["translated_date_parts"] == 2
    assert diagnostics["changed_literals"] == 0


def test_translates_exact_sqlite_year_extraction_for_postgres():
    original = "SELECT * FROM events WHERE strftime('%Y', happened_at) = '2012'"

    sql, diagnostics = normalize(
        "Show events in 2012.",
        original,
        dialect="postgresql",
    )

    assert "EXTRACT(YEAR FROM happened_at)" in sql
    assert diagnostics["translated_date_parts"] == 1


def test_non_year_strftime_format_is_unchanged():
    original = "SELECT * FROM events WHERE strftime('%m', happened_at) = '08'"

    sql, diagnostics = normalize("Show August events.", original)

    assert sql == original
    assert diagnostics["reason"] == "no-supported-dialect-date-part"


def test_strftime_with_extra_arguments_is_unchanged():
    original = "SELECT * FROM events WHERE strftime('%Y', happened_at, 'utc') = '2012'"

    sql, diagnostics = normalize("Show events in 2012.", original)

    assert sql == original
    assert diagnostics["reason"] == "no-supported-dialect-date-part"


def test_similarly_named_function_is_unchanged():
    original = "SELECT * FROM events WHERE my_strftime('%Y', happened_at) = '2012'"

    sql, diagnostics = normalize("Show events in 2012.", original)

    assert sql == original
    assert diagnostics["reason"] == "no-explicit-full-year-span"


def test_year_span_and_date_part_can_normalize_together():
    original = (
        "SELECT * FROM schools WHERE academic_year = '2014-15' "
        "AND strftime('%Y', opened_at) = '2014'"
    )

    sql, diagnostics = normalize(
        "Show the 2014-2015 academic year.",
        original,
    )

    assert "academic_year = '2014-2015'" in sql
    assert "YEAR(opened_at)" in sql
    assert diagnostics["changed_literals"] == 1
    assert diagnostics["translated_date_parts"] == 1
