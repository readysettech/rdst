import hashlib
from copy import deepcopy
import pytest
from features.ask.occurrence_percentage import has_percentage_authorization

Q = "What percentage of associated customers are active?"
SQL = "SELECT CAST(100*n AS DOUBLE)/d FROM ratios"
DIAG = {
    "status": "activate",
    "verdict": "activate",
    "selected_intents": ["percentage_output"],
    "selected_intent": "percentage_output",
    "activation_allowed": True,
    "shadow": False,
    "selected_sql_applied": True,
    "router_called_before_application": True,
    "effective_question_sha256": hashlib.sha256(Q.encode()).hexdigest(),
    "selected_sql_sha256": hashlib.sha256(SQL.encode()).hexdigest(),
}


def test_applied_percentage_decision_bound_to_question_and_sql():
    assert has_percentage_authorization(Q, SQL, DIAG)
    assert not has_percentage_authorization(Q + " Count customers instead.", SQL, DIAG)
    assert not has_percentage_authorization(Q, SQL + " WHERE d>0", DIAG)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "no_match"),
        ("verdict", "abstain"),
        ("selected_intents", ["ratio_output"]),
        ("selected_intents", []),
        ("selected_intents", ["percentage_output", "ratio_output"]),
        ("selected_intents", ["percentage_output", "percentage_output"]),
        ("selected_intents", ["unknown"]),
        ("selected_intent", "ratio_output"),
        ("activation_allowed", False),
        ("activation_allowed", 1),
        ("shadow", True),
        ("shadow", 0),
        ("selected_sql_applied", False),
        ("selected_sql_applied", 1),
        ("router_called_before_application", False),
        ("router_called_before_application", 1),
        ("effective_question_sha256", ""),
        ("selected_sql_sha256", ""),
    ],
)
def test_missing_unapplied_or_incompatible_authority_abstains(field, value):
    diagnostic = deepcopy(DIAG)
    diagnostic[field] = value
    assert not has_percentage_authorization(Q, SQL, diagnostic)


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "verdict",
        "activation_allowed",
        "shadow",
        "selected_sql_applied",
        "router_called_before_application",
        "effective_question_sha256",
        "selected_sql_sha256",
    ],
)
def test_missing_required_provenance_abstains(field):
    diagnostic = deepcopy(DIAG)
    del diagnostic[field]
    assert not has_percentage_authorization(Q, SQL, diagnostic)


@pytest.mark.parametrize("diagnostic", [None, [], True, "percentage_output", {}])
def test_malformed_provenance_abstains(diagnostic):
    assert not has_percentage_authorization(Q, SQL, diagnostic)
