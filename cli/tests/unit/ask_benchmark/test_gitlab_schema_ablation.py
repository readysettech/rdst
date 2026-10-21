from devtools.ask_benchmark.gitlab_schema_ablation import (
    CASES,
    _sql_tables,
    _summary,
)


def test_gitlab_diagnostic_has_twelve_unique_cases():
    assert len(CASES) == 12
    assert len({case.case_id for case in CASES}) == len(CASES)
    assert all(case.required_tables for case in CASES)


def test_sql_table_extraction_excludes_cte_names():
    assert _sql_tables(
        "WITH recent AS (SELECT id FROM projects) "
        "SELECT recent.id FROM recent JOIN namespaces ON namespaces.id = recent.id"
    ) == {"projects", "namespaces"}


def test_summary_excludes_unscored_budget_stops():
    def attempt(condition: str, *, scored: bool) -> dict[str, object]:
        return {
            "case_id": 1 if scored else 2,
            "condition": condition,
            "scored": scored,
            "success": scored,
            "error": None if scored else "cost limit reached",
            "schema_required_table_recall": 1.0 if scored else 0.0,
            "generated_required_table_recall": 1.0 if scored else 0.0,
            "schema_tables": ["projects"] if scored else [],
            "latency_ms": 100.0 if scored else 0.0,
            "input_tokens": 10 if scored else 0,
            "output_tokens": 2 if scored else 0,
            "normalized_cold_cost_usd": "0.01" if scored else "0",
        }

    summary = _summary(
        {
            "run_id": "run",
            "purpose": "diagnostic",
            "table_count": 2,
            "formatted_schema_chars": 100,
            "compact_formatted_schema_chars": 60,
            "compact_to_verbose_char_ratio": 0.6,
            "conditions": ["full", "compact"],
        },
        [
            attempt("full", scored=True),
            attempt("compact", scored=True),
            attempt("full", scored=False),
            attempt("compact", scored=False),
        ],
    )

    assert summary["completed_paired_case_count"] == 1
    assert len(summary["unscored_stops"]) == 2
    assert all(row["attempts"] == 1 for row in summary["conditions"])
    assert all(row["mean_schema_tables"] == 1 for row in summary["conditions"])


def test_summary_uses_manifest_conditions():
    summary = _summary(
        {
            "run_id": "run",
            "purpose": "diagnostic",
            "table_count": 2,
            "formatted_schema_chars": 100,
            "compact_formatted_schema_chars": 60,
            "compact_to_verbose_char_ratio": 0.6,
            "conditions": ["full", "compact"],
        },
        [],
    )

    assert [row["condition"] for row in summary["conditions"]] == [
        "full",
        "compact",
    ]


def test_attempt_constructs_ask_with_the_product_profile(monkeypatch):
    from devtools.ask_benchmark import gitlab_schema_ablation
    from features.ask.profiles import default_ask_service_options

    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def ask(self, _input, _options):
            return
            yield

    class FakeAdapter:
        def set_attempt_id(self, _attempt_id):
            pass

        def drain_call_records(self):
            return []

    monkeypatch.setattr(gitlab_schema_ablation, "AskService", FakeService)

    gitlab_schema_ablation._run_attempt(
        case=CASES[0],
        condition="verbose",
        semantic_manager=object(),
        adapter=FakeAdapter(),
        schema_sha256="0" * 64,
        schema_prompt_sha256="1" * 64,
    )

    options = default_ask_service_options()
    assert {key: captured[key] for key in options} == options
