from __future__ import annotations

from types import SimpleNamespace

from features.ask.dual_candidate import (
    alternate_generation_reasons,
    assess_candidate,
    generate_and_select,
    select_candidate,
)


class FakeContext(SimpleNamespace):
    def add_llm_call(self, **call):
        self.calls.append(call)


class FakeManager:
    def __init__(self, text: str):
        self.text = text
        self.query_count = 0

    def query(self, **_kwargs):
        self.query_count += 1
        return {
            "text": self.text,
            "model": "claude-sonnet-4-6",
            "usage": {"total_tokens": 10},
        }


def _schema(**tables):
    return SimpleNamespace(
        tables={
            table_name: SimpleNamespace(
                name=table_name,
                columns={column: SimpleNamespace(name=column) for column in columns},
            )
            for table_name, columns in tables.items()
        }
    )


def test_unsupported_predicate_literal_selects_grounded_alternate():
    selected, diagnostics = select_candidate(
        question="Show all bonds for atom 1.",
        provided_context="",
        primary_sql="SELECT * FROM bond WHERE atom_id = 1 OR atom_id = 2",
        alternate_sql="SELECT * FROM bond WHERE atom_id = 1",
        dialect="mysql",
    )
    assert selected == "alternate"
    assert diagnostics["primary"]["unsupported_literals"] == ("2",)


def test_equivalent_date_spelling_is_grounded():
    assessment = assess_candidate(
        question="How many transactions happened after 2012/1/1?",
        provided_context="",
        sql="SELECT COUNT(*) FROM transaction WHERE date > '2012-01-01'",
        dialect="mysql",
    )
    assert assessment.unsupported_literals == ()


def test_equivalent_academic_year_span_is_grounded():
    assessment = assess_candidate(
        question="Show enrollment in the 2014-2015 academic year.",
        provided_context="",
        sql="SELECT enrollment FROM school WHERE academic_year = '2014-15'",
        dialect="mysql",
    )
    assert assessment.unsupported_literals == ()


def test_explicit_positive_percentage_adjustment_factor_is_grounded():
    assessment = assess_candidate(
        question="How many values are 20% higher than average?",
        provided_context="",
        sql=(
            "SELECT COUNT(*) FROM measurement WHERE value > "
            "(SELECT AVG(value) * 1.2 FROM measurement)"
        ),
        dialect="postgresql",
    )
    assert assessment.unsupported_literals == ()


def test_wrong_percentage_adjustment_factor_remains_unsupported():
    assessment = assess_candidate(
        question="How many values are 20% lower than average?",
        provided_context="",
        sql=(
            "SELECT COUNT(*) FROM measurement WHERE value < "
            "(SELECT AVG(value) * 1.2 FROM measurement)"
        ),
        dialect="postgresql",
    )
    assert assessment.unsupported_literals == ("1.2",)


def test_percentage_factor_uses_the_nearest_direction():
    assessment = assess_candidate(
        question=(
            "Metric A is 20% lower than average, while metric B is 50% higher. "
            "Count rows where metric A crosses its threshold."
        ),
        provided_context="",
        sql=(
            "SELECT COUNT(*) FROM measurement WHERE metric_a < "
            "(SELECT AVG(metric_a) * 1.2 FROM measurement)"
        ),
        dialect="postgresql",
    )
    assert assessment.unsupported_literals == ("1.2",)


def test_sql_date_format_string_remains_unsupported():
    assessment = assess_candidate(
        question="Show transactions in June 2013.",
        provided_context="June 2013 refers to 201306 in yearmonth.Date.",
        sql=("SELECT id FROM transaction WHERE DATE_FORMAT(date, '%Y%m') = '201306'"),
        dialect="mysql",
    )
    assert assessment.unsupported_literals == ("%Y%m",)


def test_evidence_identifier_reward_preserves_primary():
    selected, _ = select_candidate(
        question="Show the eye, hair, and skin colors for each hero.",
        provided_context="eye color refers to eye_colour_id; hair color refers to hair_colour_id; skin color refers to skin_colour_id",
        primary_sql="SELECT eye_colour_id, hair_colour_id, skin_colour_id FROM superhero",
        alternate_sql="SELECT eye_colour, hair_colour, skin_colour FROM superhero",
        dialect="postgresql",
    )
    assert selected == "primary"


def test_unrequested_limit_selects_complete_alternate():
    selected, diagnostics = select_candidate(
        question="Which card sets have foreign data?",
        provided_context="",
        primary_sql="SELECT DISTINCT set_id FROM foreign_data LIMIT 1",
        alternate_sql="SELECT DISTINCT set_id FROM foreign_data",
        dialect="mysql",
    )
    assert selected == "alternate"
    assert diagnostics["primary"]["unrequested_limit"] is True


def test_tie_keeps_primary():
    selected, _ = select_candidate(
        question="List names.",
        provided_context="",
        primary_sql="SELECT name FROM person",
        alternate_sql="SELECT full_name FROM people",
        dialect="mysql",
    )
    assert selected == "primary"


def test_aggregation_shape_disagreement_keeps_primary():
    selected, diagnostics = select_candidate(
        question="How many students are enrolled?",
        provided_context="academic year is 2014-2015",
        primary_sql="SELECT enrollment FROM school WHERE academic_year = '2014-15'",
        alternate_sql="SELECT SUM(enrollment) FROM school WHERE academic_year = '2014-2015'",
        dialect="mysql",
    )
    assert selected == "primary"
    assert diagnostics["aggregation_shape_matches"] is False


def test_generation_selects_alternate_and_records_call():
    ctx = FakeContext(
        sql="SELECT name FROM person LIMIT 1",
        generated_sql="SELECT name FROM person LIMIT 1",
        sql_explanation="primary",
        question="List names.",
        refined_question="",
        provided_context="",
        conversation_context="",
        db_type="mysql",
        schema_formatted="Table person: name text\n",
        query_grounding_block="",
        matched_database_values=(),
        calls=[],
        dual_candidate_selection={},
    )
    result = generate_and_select(
        ctx,
        FakeManager("```sql\nSELECT name FROM person\n```"),
    )
    assert result.sql == "SELECT name FROM person"
    assert result.dual_candidate_selection["selected"] == "alternate"
    assert result.calls[0]["phase"] == "alternate_generate"


def test_invalid_alternate_fails_open():
    ctx = FakeContext(
        sql="SELECT name FROM person",
        generated_sql="SELECT name FROM person",
        sql_explanation="primary",
        question="List names.",
        refined_question="",
        provided_context="",
        conversation_context="",
        db_type="mysql",
        schema_formatted="Table person: name text\n",
        query_grounding_block="",
        matched_database_values=(),
        calls=[],
        dual_candidate_selection={},
    )
    result = generate_and_select(ctx, FakeManager("DELETE FROM person"))
    assert result.sql == "SELECT name FROM person"
    assert result.dual_candidate_selection["status"] == "alternate_error"


def test_safe_primary_skips_alternate_generation():
    manager = FakeManager("SELECT other_name FROM person")
    ctx = FakeContext(
        sql="SELECT name FROM person",
        generated_sql="SELECT name FROM person",
        sql_explanation="primary",
        question="List names.",
        refined_question="",
        provided_context="",
        conversation_context="",
        db_type="mysql",
        schema_formatted="Table person: name text\n",
        schema_info=_schema(person=("name",)),
        query_grounding_block="",
        matched_database_values=(),
        calls=[],
        dual_candidate_selection={},
    )

    result = generate_and_select(ctx, manager)

    assert result.sql == "SELECT name FROM person"
    assert manager.query_count == 0
    assert result.dual_candidate_selection["status"] == "primary_sufficient"
    assert result.dual_candidate_selection["alternate_generation_skipped"] is True


def test_gate_generates_for_weak_predicate():
    reasons = alternate_generation_reasons(
        question="List person identifiers.",
        provided_context="person identifier refers to person_id; active means 'active'",
        primary_sql="SELECT person_id FROM person WHERE status_code = 'active'",
        dialect="mysql",
        schema_info=_schema(person=("person_id", "status_code")),
    )
    assert "weakly-grounded-predicates" in reasons


def test_gate_skips_weak_predicate_when_primary_is_not_otherwise_grounded():
    reasons = alternate_generation_reasons(
        question="List people.",
        provided_context="active means 'active'",
        primary_sql="SELECT name FROM person WHERE status_code = 'active'",
        dialect="mysql",
        schema_info=_schema(person=("name", "status_code")),
    )
    assert reasons == ()


def test_gate_keeps_weak_predicate_for_complex_aggregate_semantics():
    reasons = alternate_generation_reasons(
        question="How many customers have monthly consumption over 1000?",
        provided_context="",
        primary_sql=(
            "SELECT COUNT(DISTINCT customer_id) FROM usage "
            "GROUP BY customer_id "
            "HAVING SUM(consumption) / COUNT(DISTINCT usage_date) > 1000"
        ),
        dialect="mysql",
        schema_info=_schema(
            usage=("customer_id", "consumption", "usage_date"),
        ),
    )
    assert "weakly-grounded-predicates" in reasons


def test_gate_generates_for_unsupported_literal():
    reasons = alternate_generation_reasons(
        question="Show bonds for atom 1.",
        provided_context="",
        primary_sql="SELECT * FROM bond WHERE atom_id = 2",
        dialect="mysql",
        schema_info=_schema(bond=("atom_id",)),
    )
    assert "unsupported-literals" in reasons


def test_gate_generates_for_unrequested_limit():
    reasons = alternate_generation_reasons(
        question="List names.",
        provided_context="",
        primary_sql="SELECT name FROM person LIMIT 1",
        dialect="mysql",
        schema_info=_schema(person=("name",)),
    )
    assert "unrequested-limit" in reasons


def test_gate_skips_when_only_aggregate_penalty_is_high():
    reasons = alternate_generation_reasons(
        question="List names.",
        provided_context="",
        primary_sql=(
            "SELECT name FROM a UNION SELECT name FROM b UNION SELECT name FROM c"
        ),
        dialect="mysql",
        schema_info=_schema(a=("name",), b=("name",), c=("name",)),
    )
    assert reasons == ()


def test_gate_skips_necessary_join_keys_despite_high_penalty():
    reasons = alternate_generation_reasons(
        question=(
            "What is the average crime count for regions that have accounts "
            "opened since 1997?"
        ),
        provided_context="A15 is the crime count.",
        primary_sql=(
            "SELECT AVG(d.A15) FROM district AS d WHERE d.district_id IN "
            "(SELECT a.district_id FROM account AS a "
            "WHERE YEAR(a.date) >= 1997)"
        ),
        dialect="mysql",
        schema_info=_schema(
            district=("district_id", "A15"),
            account=("district_id", "date"),
        ),
    )
    assert reasons == ()


def test_gate_generates_for_co_located_join_opportunity():
    reasons = alternate_generation_reasons(
        question="List foreign names and languages.",
        provided_context="",
        primary_sql=(
            "SELECT fd.name, fd.language FROM foreign_data AS fd "
            "JOIN cards AS c ON c.id = fd.card_id"
        ),
        dialect="mysql",
        schema_info=_schema(
            cards=("id", "set_id"),
            foreign_data=("card_id", "name", "language", "set_id"),
        ),
    )
    assert "co-located-join-opportunity" in reasons


def test_gate_skips_co_located_join_when_primary_is_strongly_evidence_grounded():
    reasons = alternate_generation_reasons(
        question="List foreign names and languages.",
        provided_context="foreign name refers to name; language refers to language",
        primary_sql=(
            "SELECT fd.name, fd.language FROM foreign_data AS fd "
            "JOIN cards AS c ON c.id = fd.card_id"
        ),
        dialect="mysql",
        schema_info=_schema(
            cards=("id", "set_id"),
            foreign_data=("card_id", "name", "language", "set_id"),
        ),
    )
    assert reasons == ()


def test_gate_skips_legitimate_distributed_join():
    reasons = alternate_generation_reasons(
        question="List card names and foreign languages.",
        provided_context="",
        primary_sql=(
            "SELECT c.name, fd.language FROM cards AS c "
            "JOIN foreign_data AS fd ON fd.card_id = c.id"
        ),
        dialect="mysql",
        schema_info=_schema(
            cards=("id", "name"),
            foreign_data=("card_id", "language"),
        ),
    )
    assert reasons == ()


def test_gate_counts_literal_filters_inside_join_conditions():
    reasons = alternate_generation_reasons(
        question="List hero names whose eye colour is black.",
        provided_context="",
        primary_sql=(
            "SELECT s.superhero_name FROM superhero AS s "
            "JOIN colour AS c ON s.eye_colour_id = c.id AND c.colour = 'Black'"
        ),
        dialect="mysql",
        schema_info=_schema(
            superhero=("superhero_name", "eye_colour_id"),
            colour=("id", "colour"),
        ),
    )
    assert reasons == ()


def test_gate_does_not_treat_count_star_as_co_located_projection():
    reasons = alternate_generation_reasons(
        question="How many members major in physics?",
        provided_context="physics refers to major_name = 'Physics'",
        primary_sql=(
            "SELECT COUNT(*) FROM member JOIN major "
            "ON member.major_id = major.id WHERE major.major_name = 'Physics'"
        ),
        dialect="mysql",
        schema_info=_schema(
            member=("member_id", "major_id"),
            major=("id", "major_name"),
        ),
    )
    assert reasons == ()


def test_gate_fails_open_when_schema_is_missing():
    reasons = alternate_generation_reasons(
        question="List names.",
        provided_context="",
        primary_sql="SELECT name FROM person",
        dialect="postgresql",
        schema_info=None,
    )
    assert reasons == ("schema-assessment-error",)


def test_gate_fails_open_when_primary_cannot_be_parsed():
    reasons = alternate_generation_reasons(
        question="List names.",
        provided_context="",
        primary_sql="not sql at all",
        dialect="mysql",
        schema_info=_schema(person=("name",)),
    )
    assert reasons == ("assessment-error",)
