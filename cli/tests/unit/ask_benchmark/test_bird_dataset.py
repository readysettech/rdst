import json
from pathlib import Path

import pytest

from devtools.ask_benchmark.bird_dataset import (
    MYSQL_INVALID_GOLD_CASE_IDS,
    PIPELINE_SMOKE_CASE_IDS,
    DatasetError,
    exclude_invalid_gold_cases,
    load_cases,
    partition_provenance,
    select_canary,
    select_holdout,
    select_pipeline_smoke,
)
from devtools.ask_benchmark.models import BenchmarkCase


def test_load_cases_reads_bird_field_names(tmp_path: Path):
    path = tmp_path / "mini.json"
    path.write_text(
        json.dumps(
            [
                {
                    "question_id": 1471,
                    "db_id": "cards",
                    "question": "How many customers?",
                    "evidence": "customers means distinct customer IDs",
                    "SQL": "SELECT COUNT(DISTINCT customer_id) FROM customers",
                    "difficulty": "simple",
                }
            ]
        ),
        encoding="utf-8",
    )

    cases = load_cases(path, dialect="mysql")

    assert cases == [
        BenchmarkCase(
            question_id=1471,
            db_id="cards",
            question="How many customers?",
            evidence="customers means distinct customer IDs",
            gold_sql="SELECT COUNT(DISTINCT customer_id) FROM customers",
            difficulty="simple",
            dialect="mysql",
        )
    ]


def test_invalid_mysql_gold_is_preserved_then_excluded(tmp_path: Path):
    path = tmp_path / "mini.json"
    path.write_text(
        json.dumps(
            [
                {
                    "question_id": 208,
                    "db_id": "toxicology",
                    "question": "Which label?",
                    "evidence": "",
                    "SQL": "SELECT `T`.`label` FROM molecule AS `t`",
                    "difficulty": "simple",
                }
            ]
        ),
        encoding="utf-8",
    )

    mysql_cases = load_cases(path, dialect="mysql")
    postgres_cases = load_cases(path, dialect="postgresql")

    assert mysql_cases[0].gold_sql == "SELECT `T`.`label` FROM molecule AS `t`"
    assert exclude_invalid_gold_cases(mysql_cases) == []
    assert exclude_invalid_gold_cases(postgres_cases) == postgres_cases
    assert 208 in MYSQL_INVALID_GOLD_CASE_IDS


def test_load_cases_rejects_duplicate_question_ids(tmp_path: Path):
    item = {
        "question_id": 1,
        "db_id": "db",
        "question": "q",
        "evidence": "e",
        "SQL": "SELECT 1",
        "difficulty": "simple",
    }
    path = tmp_path / "mini.json"
    path.write_text(json.dumps([item, item]), encoding="utf-8")

    with pytest.raises(DatasetError, match="duplicate"):
        load_cases(path, dialect="mysql")


def test_canary_round_robins_database_and_difficulty_groups():
    cases = [
        BenchmarkCase(
            question_id=index,
            db_id=f"db{index % 3}",
            question=f"q{index}",
            evidence="",
            gold_sql="SELECT 1",
            difficulty=("simple", "moderate")[index % 2],
            dialect="mysql",
        )
        for index in range(60)
    ]

    selected = select_canary(cases, size=12)

    assert len(selected) == 12
    assert {(case.db_id, case.difficulty) for case in selected} == {
        (case.db_id, case.difficulty) for case in cases
    }
    assert selected == select_canary(cases, size=12)


def test_holdout_is_exact_disjoint_complement_of_development_partition():
    cases = [
        BenchmarkCase(
            question_id=index,
            db_id=f"db{index % 4}",
            question=f"q{index}",
            evidence="",
            gold_sql="SELECT 1",
            difficulty=("simple", "moderate", "challenging")[index % 3],
            dialect="mysql",
        )
        for index in range(500)
    ]

    development = select_canary(cases)
    holdout = select_holdout(cases)

    assert len(development) == 50
    assert len(holdout) == 450
    assert {case.question_id for case in development}.isdisjoint(
        case.question_id for case in holdout
    )
    assert {case.question_id for case in development + holdout} == set(range(500))

    provenance = partition_provenance(cases)
    assert provenance["development_size"] == 50
    assert provenance["holdout_size"] == 450
    assert provenance == partition_provenance(reversed(cases))


def test_pipeline_smoke_is_fixed_and_restricted_to_development_cases():
    ids = list(PIPELINE_SMOKE_CASE_IDS) + list(range(2_000, 2_034))
    cases = [
        BenchmarkCase(
            question_id=question_id,
            db_id="fixture",
            question="q",
            evidence="",
            gold_sql="SELECT 1",
            difficulty="simple",
            dialect="mysql",
        )
        for question_id in reversed(ids)
    ]

    selected = select_pipeline_smoke(cases)

    assert [case.question_id for case in selected] == list(PIPELINE_SMOKE_CASE_IDS)
    with pytest.raises(DatasetError, match="missing from the dataset"):
        select_pipeline_smoke(cases[:-1])
