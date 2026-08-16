import json

import pytest

from devtools.ask_benchmark.bird_dataset import (
    MYSQL_INVALID_GOLD_CASE_IDS,
    SCORABLE_CASE_COUNT,
)
from devtools.ask_benchmark.gold_replay import (
    GoldReplayError,
    compare_gold_replays,
    replay_gold_results,
    verify_replay_provenance,
    verify_replay_receipt,
)
from devtools.ask_benchmark.models import BenchmarkCase, QueryResult


def _case(question_id):
    return BenchmarkCase(
        question_id=question_id,
        db_id="fixture",
        question="question",
        evidence="",
        gold_sql="SELECT 1",
        difficulty="simple",
        dialect="mysql",
    )


class _Executor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def execute(self, _sql, *, db_id):
        assert db_id == "fixture"
        return QueryResult(rows=(next(self.rows),))


def test_replay_records_declared_float_instability_and_verifies_hash():
    receipt = replay_gold_results(
        [_case(1482)],
        _Executor(
            [
                (545.4018999791232,),
                (545.4018999791261,),
            ]
        ),
        repetitions=2,
    )

    assert receipt["observed_unstable_case_ids"] == [1482]
    assert len(receipt["fingerprints"]["1482"]) == 2


def test_replay_fails_on_undeclared_instability():
    with pytest.raises(GoldReplayError, match="undeclared unstable cases"):
        replay_gold_results(
            [_case(1)],
            _Executor([(1.0,), (1.0000000000001,)]),
            repetitions=2,
        )


def test_compare_allows_only_declared_cross_process_change():
    first = replay_gold_results([_case(1482)], _Executor([(1.0,)]), repetitions=1)
    second = replay_gold_results([_case(1482)], _Executor([(2.0,)]), repetitions=1)

    assert compare_gold_replays(first, second) == [1482]


def test_compare_rejects_stable_cross_process_change():
    first = replay_gold_results([_case(1)], _Executor([(1,)]), repetitions=1)
    second = replay_gold_results([_case(1)], _Executor([(2,)]), repetitions=1)

    with pytest.raises(GoldReplayError, match="Stable gold fingerprints changed"):
        compare_gold_replays(first, second)


def test_full_receipt_requires_current_identity_and_two_repetitions():
    cases = [
        _case(question_id)
        for question_id in range(500)
        if question_id not in MYSQL_INVALID_GOLD_CASE_IDS
    ]
    receipt = replay_gold_results(
        cases,
        _Executor([(1,)] * (SCORABLE_CASE_COUNT * 2)),
        repetitions=2,
    )

    verify_replay_receipt(receipt)

    stale = replay_gold_results(
        cases,
        _Executor([(1,)] * SCORABLE_CASE_COUNT),
        repetitions=1,
    )
    with pytest.raises(GoldReplayError, match="at least two repetitions"):
        verify_replay_receipt(stale)


def test_replay_rejects_invalid_released_mysql_gold():
    with pytest.raises(GoldReplayError, match="excluded invalid-gold"):
        replay_gold_results(
            [_case(MYSQL_INVALID_GOLD_CASE_IDS[0])],
            _Executor([(1,)]),
            repetitions=1,
        )


def test_replay_provenance_must_match_current_database():
    provision = {
        "archive_sha256": "archive",
        "mysql_image_id": "image",
        "mysql_version": "8.4.0",
        "database_prefix": "bird_",
    }
    receipt = replay_gold_results(
        [_case(1)],
        _Executor([(1,)]),
        repetitions=1,
        provenance={"database_provision": provision},
    )

    verify_replay_provenance(receipt, provision)
    with pytest.raises(GoldReplayError, match="mysql_version"):
        verify_replay_provenance(receipt, {**provision, "mysql_version": "9.0"})


def test_receipt_tampering_is_detected():
    receipt = replay_gold_results([_case(1)], _Executor([(1,)]), repetitions=1)
    tampered = json.loads(json.dumps(receipt))
    tampered["fingerprints"]["1"] = ["changed"]

    with pytest.raises(GoldReplayError, match="hash is invalid"):
        verify_replay_receipt(tampered)
