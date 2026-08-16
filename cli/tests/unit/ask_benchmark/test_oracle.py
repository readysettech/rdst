from decimal import Decimal

import pytest

from devtools.ask_benchmark.models import QueryResult
from devtools.ask_benchmark.oracle import (
    GOLD_FINGERPRINT_CODEC,
    bird_execution_correct,
    bird_soft_f1,
    result_fingerprint,
    score_results,
)


def test_exact_typed_fingerprint_is_order_independent():
    first = QueryResult(rows=((1,), (2,), (1,)))
    reordered = QueryResult(rows=((2,), (1,), (1,)))

    assert GOLD_FINGERPRINT_CODEC == "typed-exact-multiset-v1"
    assert result_fingerprint(first) == result_fingerprint(reordered)


def test_exact_typed_fingerprint_preserves_adjacent_float_difference():
    first = QueryResult(rows=((545.4018999791232,),))
    second = QueryResult(rows=((545.4018999791261,),))

    assert result_fingerprint(first) != result_fingerprint(second)


def test_exact_typed_fingerprint_normalizes_numeric_zero_and_decimal_scale():
    assert result_fingerprint(QueryResult(rows=((-0.0,),))) == result_fingerprint(
        QueryResult(rows=((0.0,),))
    )
    assert result_fingerprint(
        QueryResult(rows=((Decimal("1.00"),),))
    ) == result_fingerprint(QueryResult(rows=((Decimal(1),),)))


def test_exact_typed_fingerprint_separates_value_types():
    assert result_fingerprint(QueryResult(rows=((Decimal(1),),))) != result_fingerprint(
        QueryResult(rows=(("1",),))
    )


def test_failed_results_cannot_be_fingerprinted():
    with pytest.raises(ValueError, match="successful"):
        result_fingerprint(QueryResult(error="failed"))


def test_bird_execution_accuracy_ignores_order_and_duplicates():
    predicted = [(2,), (1,), (1,)]
    truth = [(1,), (2,)]

    assert bird_execution_correct(predicted, truth) is True


def test_diagnostics_retain_duplicate_and_order_differences():
    candidate = QueryResult(rows=((2,), (1,), (1,)))
    gold = QueryResult(rows=((1,), (2,)))

    score = score_results(candidate, gold)

    assert score.execution_correct is True
    assert score.multiset_correct is False
    assert score.ordered_correct is False


def test_soft_f1_matches_bird_row_membership_formula():
    assert bird_soft_f1([(1, 2)], [(1, 3)]) == 0.5
    assert bird_soft_f1([], []) == 1.0


def test_candidate_error_is_an_incorrect_result():
    candidate = QueryResult(error="unknown column")
    gold = QueryResult(rows=((1,),))

    score = score_results(candidate, gold)

    assert score.execution_correct is False
    assert score.soft_f1 == 0.0


def test_gold_error_cannot_be_scored():
    candidate = QueryResult(rows=((1,),))
    gold = QueryResult(error="fixture unavailable")

    try:
        score_results(candidate, gold)
    except ValueError as exc:
        assert "Gold result" in str(exc)
    else:
        raise AssertionError("Expected a gold execution error")
