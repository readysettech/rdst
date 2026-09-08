from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import pytest
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases.validate import validate_sql


def context(sql, dialect="mysql", enabled=True):
    return Ask3Context(
        question="Show matching records",
        target="local",
        db_type=dialect,
        sql=sql,
        enforce_result_limit=False,
        resolved_column_validation_enabled=enabled,
        schema_info=NS(
            tables={
                "parcels": NS(
                    columns={
                        k: NS(data_type=t)
                        for k, t in {
                            "id": "int",
                            "address": "text",
                            "customer_id": "int",
                        }.items()
                    }
                ),
                "customers": NS(
                    columns={
                        k: NS(data_type=t)
                        for k, t in {"id": "int", "email": "text"}.items()
                    }
                ),
            }
        ),
    )


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT missing.address FROM parcels p",
        "SELECT absent_field FROM parcels p",
        "SELECT id FROM parcels p JOIN customers c ON p.customer_id=c.id",
        "WITH chosen AS (SELECT id FROM parcels) SELECT x.id FROM chosen c",
        "SELECT x.email FROM parcels p JOIN customers c ON p.customer_id=c.id",
    ],
)
def test_resolved_check_rejects_missing_or_ambiguous_bindings(sql, dialect):
    c = validate_sql(context(sql, dialect), MagicMock())
    assert c.has_validation_errors()
    assert "Column resolution failed:" in c.validation_errors[0].message
    assert c.sql == sql


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT p.address FROM parcels p",
        "SELECT c.email FROM parcels p JOIN customers c ON p.customer_id=c.id",
        "WITH chosen AS (SELECT id FROM parcels) SELECT c.id FROM chosen c",
        "SELECT p.id FROM parcels p WHERE EXISTS (SELECT 1 FROM customers c WHERE c.id=p.customer_id)",
        "SELECT id FROM parcels p JOIN customers c USING(id)",
        "SELECT customer_id, COUNT(*) AS total FROM parcels GROUP BY customer_id ORDER BY total DESC",
        "SELECT d.id FROM (SELECT id FROM parcels) d",
    ],
)
def test_resolved_check_preserves_valid_nested_correlated_and_join_sql(sql, dialect):
    c = validate_sql(context(sql, dialect), MagicMock())
    assert not c.has_validation_errors(), c.validation_errors
    assert c.sql == sql


def test_resolved_check_is_opt_in_and_round_trips_context():
    sql = "SELECT missing.address FROM parcels p"
    assert not validate_sql(
        context(sql, enabled=False), MagicMock()
    ).has_validation_errors()
    c = context(sql)
    assert Ask3Context.from_dict(c.to_dict()).resolved_column_validation_enabled
    old = c.to_dict()
    old.pop("resolved_column_validation_enabled")
    assert not Ask3Context.from_dict(old).resolved_column_validation_enabled


def test_existing_legacy_errors_are_preserved_without_another_repair_input():
    sql = "SELECT p.unknown_field FROM parcels p"
    old = validate_sql(context(sql, enabled=False), MagicMock())
    new = validate_sql(context(sql, enabled=True), MagicMock())
    assert (
        old.validation_errors == new.validation_errors and old.has_validation_errors()
    )
