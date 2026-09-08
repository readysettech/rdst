import itertools
import pytest
from features.ask.list_membership import plan_list_membership, proved_other_count

BASE = "SELECT COUNT(*) FROM assets WHERE active=1 AND ({})"
PARTS = ["tags IS NULL", "tags=''", "FIND_IN_SET('Blue',tags)=0"]


@pytest.mark.parametrize("parts", list(itertools.permutations(PARTS)))
def test_three_branches_in_any_order(parts):
    plan = plan_list_membership(BASE.format(" OR ".join(parts)), "mysql")
    assert plan and plan.predicate_kind == "null_empty_set_absence"
    assert "active = 1" in plan.candidate_sql and "tags <> 'Blue'" in plan.candidate_sql


@pytest.mark.parametrize(
    "predicate",
    [
        "tags IS NULL OR other='' OR FIND_IN_SET('Blue',tags)=0",
        "other IS NULL OR tags='' OR FIND_IN_SET('Blue',tags)=0",
        "tags IS NULL OR tags IS NULL OR FIND_IN_SET('Blue',tags)=0",
        "tags='' OR tags='' OR FIND_IN_SET('Blue',tags)=0",
        "tags IS NULL OR tags=' ' OR FIND_IN_SET('Blue',tags)=0",
        "tags IS NULL OR tags='' OR tags NOT LIKE '%Blue%'",
        "tags IS NULL OR tags='' OR FIND_IN_SET('Blue',tags)=0 OR active=1",
        "tags IS NULL OR tags='' OR FIND_IN_SET('Blue',tags)>0",
        "tags IS NULL OR tags='' OR FIND_IN_SET('Blue',tags)=0 OR FIND_IN_SET('Red',tags)=0",
        "(tags IS NULL OR tags='') AND FIND_IN_SET('Blue',tags)=0",
        "tags='' OR FIND_IN_SET('Blue',tags)=0",
    ],
)
def test_unsupported_branches_abstain(predicate):
    assert plan_list_membership(BASE.format(predicate), "mysql") is None


@pytest.mark.parametrize(
    "values",
    [
        [[b"Blue", 5, 0, 0], [b"Blue,Green", 2, 0, 2], [b"", 1, 1, 1]],
        [[b"Blue", 5, 0, 0], [b"Blue,Blue", 2, 0, 2]],
        [[b"Blue", 5, 0, 0], [b"Blue, Green", 2, 0, 2]],
    ],
)
def test_empty_or_invalid_native_storage_is_not_reinterpreted(values):
    plan = plan_list_membership(BASE.format(" OR ".join(PARTS)), "mysql")
    assert proved_other_count(plan, [[sum(x[2] for x in values)]], values) is None
