import asyncio
from unittest.mock import MagicMock, patch

import pytest

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases.validate import validate_sql
from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo
from features.ask.null_ordering import place_nulls_last
from features.ask.profiles import default_ask_service_options


def schema():
    columns = {
        "tconst": ColumnInfo("tconst", "integer", is_primary_key=True),
        "primarytitle": ColumnInfo("primarytitle", "text"),
        "runtimeminutes": ColumnInfo("runtimeminutes", "integer"),
    }
    return SchemaInfo(
        target="imdb",
        db_type="postgresql",
        tables={"title_basics": TableInfo(name="title_basics", columns=columns)},
    )


@pytest.mark.parametrize(
    "db_type,sql,expected",
    [
        (
            "postgresql",
            "SELECT primarytitle, runtimeminutes FROM title_basics "
            "ORDER BY runtimeminutes DESC LIMIT 3",
            "SELECT primarytitle, runtimeminutes FROM title_basics "
            "ORDER BY runtimeminutes DESC NULLS LAST LIMIT 3",
        ),
        (
            "mysql",
            "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes LIMIT 3",
            "SELECT primarytitle FROM title_basics "
            "ORDER BY runtimeminutes IS NULL, runtimeminutes LIMIT 3",
        ),
        (
            # The alias stays as written: only_full_group_by rejects the GROUP BY expression
            # spelled out inside an ORDER BY expression.
            "mysql",
            "SELECT FLOOR(startyear / 10) * 10 AS decade, COUNT(*) AS title_count "
            "FROM title_basics GROUP BY FLOOR(startyear / 10) * 10 ORDER BY decade ASC LIMIT 100",
            "SELECT FLOOR(startyear / 10) * 10 AS decade, COUNT(*) AS title_count "
            "FROM title_basics GROUP BY FLOOR(startyear / 10) * 10 "
            "ORDER BY decade IS NULL, decade ASC LIMIT 100",
        ),
        (
            "postgresql",
            "SELECT b.primarytitle FROM title_basics b "
            "ORDER BY b.runtimeminutes DESC, b.tconst LIMIT 5 OFFSET 5",
            "SELECT b.primarytitle FROM title_basics AS b "
            "ORDER BY b.runtimeminutes DESC NULLS LAST, b.tconst LIMIT 5 OFFSET 5",
        ),
        (
            "postgresql",
            "SELECT genre, AVG(runtimeminutes) AS mean FROM title_basics GROUP BY genre "
            "ORDER BY mean DESC, COUNT(*) DESC LIMIT 3",
            "SELECT genre, AVG(runtimeminutes) AS mean FROM title_basics GROUP BY genre "
            "ORDER BY mean DESC NULLS LAST, COUNT(*) DESC LIMIT 3",
        ),
        (
            "postgresql",
            "SELECT * FROM (SELECT primarytitle, runtimeminutes FROM title_basics "
            "ORDER BY runtimeminutes DESC LIMIT 3) AS top ORDER BY primarytitle",
            "SELECT * FROM (SELECT primarytitle, runtimeminutes FROM title_basics "
            "ORDER BY runtimeminutes DESC NULLS LAST LIMIT 3) AS top ORDER BY primarytitle",
        ),
        (
            "postgresql",
            "SELECT t.tconst FROM ratings r LEFT JOIN title_basics t ON t.tconst = r.tconst "
            "ORDER BY t.tconst DESC LIMIT 3",
            "SELECT t.tconst FROM ratings AS r LEFT JOIN title_basics AS t "
            "ON t.tconst = r.tconst ORDER BY t.tconst DESC NULLS LAST LIMIT 3",
        ),
        # A select-list position ranks that item.
        (
            "mysql",
            "SELECT tconst, runtimeminutes FROM title_basics ORDER BY 2 ASC LIMIT 5",
            "SELECT tconst, runtimeminutes FROM title_basics "
            "ORDER BY runtimeminutes IS NULL, 2 ASC LIMIT 5",
        ),
        (
            "mysql",
            "SELECT genre, AVG(runtimeminutes) AS mean FROM title_basics GROUP BY genre "
            "ORDER BY 2 LIMIT 3",
            "SELECT genre, AVG(runtimeminutes) AS mean FROM title_basics GROUP BY genre "
            "ORDER BY mean IS NULL, 2 LIMIT 3",
        ),
        (
            "mysql",
            "SELECT genre, MIN(runtimeminutes) AS shortest FROM title_basics GROUP BY genre "
            "ORDER BY genre LIMIT 3",
            "SELECT genre, MIN(runtimeminutes) AS shortest FROM title_basics GROUP BY genre "
            "ORDER BY genre IS NULL, genre LIMIT 3",
        ),
        (
            "mysql",
            "SELECT genre FROM title_basics GROUP BY genre ORDER BY AVG(runtimeminutes) LIMIT 3",
            "SELECT genre FROM title_basics GROUP BY genre "
            "ORDER BY AVG(runtimeminutes) IS NULL, AVG(runtimeminutes) LIMIT 3",
        ),
        (
            "postgresql",
            "SELECT primarytitle, runtimeminutes FROM title_basics ORDER BY 2 DESC LIMIT 3",
            "SELECT primarytitle, runtimeminutes FROM title_basics "
            "ORDER BY 2 DESC NULLS LAST LIMIT 3",
        ),
    ],
)
def test_bounded_ranking_sorts_nulls_after_values(db_type, sql, expected):
    rewritten = place_nulls_last(sql, db_type, schema())
    assert rewritten == expected
    assert place_nulls_last(rewritten, db_type, schema()) is None


@pytest.mark.parametrize(
    "db_type,sql",
    [
        # The dialect already sorts NULL last in this direction.
        ("postgresql", "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes LIMIT 3"),
        ("mysql", "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC LIMIT 3"),
        # Written NULL placement.
        (
            "postgresql",
            "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC NULLS FIRST "
            "LIMIT 3",
        ),
        (
            "postgresql",
            "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes NULLS FIRST LIMIT 3",
        ),
        (
            "mysql",
            "SELECT primarytitle FROM title_basics "
            "ORDER BY runtimeminutes IS NULL DESC, runtimeminutes LIMIT 3",
        ),
        # Keys that are never NULL.
        ("postgresql", "SELECT tconst FROM title_basics ORDER BY tconst DESC LIMIT 3"),
        ("mysql", "SELECT tconst FROM title_basics ORDER BY tconst LIMIT 3"),
        (
            "postgresql",
            "SELECT genre FROM title_basics GROUP BY genre ORDER BY COUNT(*) DESC LIMIT 3",
        ),
        ("postgresql", "SELECT primarytitle FROM title_basics ORDER BY RANDOM() LIMIT 3"),
        ("mysql", "SELECT tconst, primarytitle FROM title_basics ORDER BY 1 LIMIT 3"),
        (
            "mysql",
            "SELECT genre, COUNT(*) AS n FROM title_basics GROUP BY genre ORDER BY 2 LIMIT 3",
        ),
        # Written back, a join without ON would become a comma join that binds looser.
        (
            "mysql",
            "SELECT g.genre, AVG(b.runtimeminutes) AS mean FROM title_basics b "
            "JOIN JSON_TABLE(CONCAT('[\"', REPLACE(b.genres, ',', '\",\"'), '\"]'), '$[*]' "
            "COLUMNS (genre VARCHAR(64) PATH '$')) AS g GROUP BY g.genre ORDER BY mean LIMIT 5",
        ),
        ("mysql", "SELECT primarytitle FROM title_basics, ratings ORDER BY runtimeminutes LIMIT 3"),
        # Grouped MySQL keys only_full_group_by would reject under an IS NULL test: an alias
        # a column of the same name hides, and an ungrouped expression.
        (
            "mysql",
            "SELECT TRIM(s.genre) AS genre, AVG(r.averagerating) AS mean FROM split s "
            "JOIN title_ratings r ON r.tconst = s.tconst GROUP BY TRIM(s.genre) "
            "ORDER BY genre LIMIT 100",
        ),
        (
            "mysql",
            "SELECT LOWER(genre) AS g, COUNT(*) AS n FROM title_basics GROUP BY genre "
            "ORDER BY LOWER(genre) LIMIT 5",
        ),
        # A position the select list does not spell out.
        ("mysql", "SELECT * FROM title_basics ORDER BY 2 LIMIT 3"),
        ("mysql", "SELECT primarytitle FROM title_basics ORDER BY 5 LIMIT 3"),
        # An unbounded ordering returns every row; NULL placement only moves them.
        ("postgresql", "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC"),
        # Unsupported dialect or SQL.
        ("sqlite", "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC LIMIT 3"),
        ("postgresql", "SELECT FROM WHERE ORDER BY"),
    ],
)
def test_orderings_that_already_rank_values_first_are_unchanged(db_type, sql):
    assert place_nulls_last(sql, db_type, schema()) is None


def test_alias_named_like_a_primary_key_is_not_assumed_present():
    sql = (
        "SELECT genre, AVG(runtimeminutes) AS tconst FROM title_basics GROUP BY genre "
        "ORDER BY tconst DESC LIMIT 3"
    )
    assert place_nulls_last(sql, "postgresql", schema()).endswith(
        "ORDER BY tconst DESC NULLS LAST LIMIT 3"
    )


def test_without_schema_primary_keys_are_still_ranked():
    sql = "SELECT tconst FROM title_basics ORDER BY tconst DESC LIMIT 3"
    assert place_nulls_last(sql, "postgresql").endswith("DESC NULLS LAST LIMIT 3")


def context(sql, enabled=True, enforce_result_limit=False):
    return Ask3Context(
        question="What are the 3 longest titles by runtime?",
        target="imdb",
        db_type="postgresql",
        sql=sql,
        enforce_result_limit=enforce_result_limit,
        ranked_nulls_last_enabled=enabled,
        schema_info=schema(),
    )


def test_validation_applies_the_stage_only_when_enabled():
    sql = "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC LIMIT 3"
    assert validate_sql(context(sql, enabled=False), MagicMock()).sql == sql
    validated = validate_sql(context(sql), MagicMock())
    assert not validated.has_validation_errors(), validated.validation_errors
    assert validated.sql.endswith("ORDER BY runtimeminutes DESC NULLS LAST LIMIT 3")
    assert validated.limit_sql == validated.sql


def test_validation_ranks_nulls_last_under_the_result_cap():
    sql = "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC"
    validated = validate_sql(context(sql, enforce_result_limit=True), MagicMock())
    assert validated.limit_added
    assert "ORDER BY runtimeminutes DESC NULLS LAST LIMIT" in validated.sql


@pytest.mark.parametrize("question", [
    "Show the 3 lowest-scored rows, treating entries with no score as the lowest",
    "Which 3 titles have the shortest runtime? Include missing runtimes first.",
    "Top 5 ratings, NULLs first",
])
def test_a_question_that_places_missing_values_keeps_the_written_order(question):
    sql = "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC LIMIT 3"
    ctx = context(sql)
    ctx.refined_question = question
    assert validate_sql(ctx, MagicMock()).sql == sql


def test_stage_flag_round_trips_and_is_on_in_the_product_profile():
    assert Ask3Context.from_dict(context("SELECT 1").to_dict()).ranked_nulls_last_enabled
    legacy = context("SELECT 1").to_dict()
    legacy.pop("ranked_nulls_last_enabled")
    assert not Ask3Context.from_dict(legacy).ranked_nulls_last_enabled
    assert default_ask_service_options()["ranked_nulls_last_enabled"] is True


def test_service_runs_the_ranking_with_nulls_last(run_blocking_inline):
    from features.ask.engine.ask3.types import ExecutionResult
    from features.ask.events import AskResultEvent
    from features.ask.models import AskInput, AskOptions
    from features.ask.service import AskService

    executed = []

    async def load_config(target):
        return target, {"engine": "postgresql", "host": "localhost"}

    def load(ctx, *_):
        ctx.schema_info = schema()
        ctx.schema_formatted = "title_basics table"
        return ctx

    def generate(ctx, *_):
        ctx.sql = "SELECT primarytitle FROM title_basics ORDER BY runtimeminutes DESC LIMIT 3"
        return ctx

    def execute(ctx, *_):
        executed.append(ctx.sql)
        ctx.execution_result = ExecutionResult(columns=["primarytitle"], rows=[("a",)])
        return ctx

    async def run():
        service = AskService(ranked_nulls_last_enabled=True, persist_queries=False)
        with (
            patch.object(service, "_load_config", side_effect=load_config),
            patch("features.ask.service.load_schema", side_effect=load),
            patch("features.ask.service.generate_sql", side_effect=generate),
            patch("features.ask.service.execute_query", side_effect=execute),
        ):
            return [
                event
                async for event in service.ask(
                    AskInput(question="Top 3 longest titles", target="imdb"),
                    AskOptions(no_interactive=True, persist_query=False),
                )
            ]

    events = asyncio.run(run())
    assert isinstance(events[-1], AskResultEvent), events[-1]
    assert executed == [events[-1].sql]
    assert events[-1].sql.endswith("ORDER BY runtimeminutes DESC NULLS LAST LIMIT 3")
