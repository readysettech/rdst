"""The operations RDST runs on Readyset-hosted inference and their contracts.

Keyservice holds every hosted system prompt and response contract.
``keyservice/scripts/generate_prompt_registry.py`` builds its ``prompt_registry.py``
from this catalog and from the LLM call sites themselves: a call site whose system
prompt and response format are literals or module constants is read directly, and
an operation whose call site builds them at run time lists them here. The hosted
client sends a hash of the system prompt and Keyservice renders its own copy, so a
prompt edited in this tree needs a regenerated registry (a unit test fails until
it is) and a Keyservice deploy before the client that sends it ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
DEFAULT_MAX_USER_BYTES = 448 * 1024

_ANALYSIS_TASK = (
    "Follow the task in the user message, which describes {subject}. "
    "Work only on that RDST task."
)
# System prompts for generate_response callers that pass none.
OPERATION_SYSTEM_PROMPTS = {
    "audit_capture_analysis": "You are RDST's database performance analyst. "
    + _ANALYSIS_TASK.format(subject="a captured database workload and its measurements"),
    "audit_health": "You are RDST's database performance analyst. "
    + _ANALYSIS_TASK.format(subject="a database health audit and its data"),
    "audit_single_target_insights": "You are RDST's database performance analyst. "
    + _ANALYSIS_TASK.format(subject="a database audit and its results"),
    "fleet_insights": "You are RDST's database performance analyst. "
    + _ANALYSIS_TASK.format(subject="an audit of a database fleet and its results"),
    "query_registry_analysis": "You are RDST's query performance analyst. "
    + _ANALYSIS_TASK.format(subject="saved queries and their measurements"),
    "sql_recovery": "You are RDST's SQL assistant. "
    + _ANALYSIS_TASK.format(subject="a database question, its SQL and the error it hit"),
    "sql_refinement": "You are RDST's SQL assistant. "
    + _ANALYSIS_TASK.format(subject="a database question, its SQL and feedback on it"),
    "sql_validation_repair": "You are RDST's SQL assistant. "
    + _ANALYSIS_TASK.format(subject="a database question and SQL that failed validation"),
}


def default_system_prompt(purpose: str | None) -> str:
    return OPERATION_SYSTEM_PROMPTS.get(str(purpose or ""), DEFAULT_SYSTEM_PROMPT)


def json_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


@dataclass(frozen=True)
class Operation:
    """One hosted operation's limits and the contracts its call sites build at run time.

    ``prompts`` and ``response_formats`` return what the generator cannot read from the
    call site; ``None`` in ``response_formats`` admits a free-text answer.
    ``schema_context`` lets a prompt carry the schema context block in front of it.
    ``legacy_prompts`` are texts that released clients send for this operation.
    """

    max_tokens: int
    max_user_bytes: int = DEFAULT_MAX_USER_BYTES
    legacy_max_user_bytes: int | None = None
    multi_turn: bool = False
    schema_context: bool = False
    prompts: Callable[[], tuple[str, ...]] | None = None
    response_formats: Callable[[], tuple[Any, ...]] | None = None
    legacy_prompts: tuple[str, ...] = ()


def _sql_generation_prompts():
    from features.ask.prompts.ask_prompts import SQL_GENERATION_SYSTEM_PROMPT

    return (SQL_GENERATION_SYSTEM_PROMPT,)


def _sql_generation_formats():
    from features.ask.sql_generation import _generation_response_format

    return (_generation_response_format(),)


def _correction_intent_formats():
    from features.ask import correction_intent_routing as module

    return (json_schema("correction_intent_routing",
                        module._response_schema(module.CORRECTION_INTENTS)),)


def _alternate_prompts():
    from features.ask.dual_candidate import ALTERNATE_SYSTEM_PROMPT

    return (ALTERNATE_SYSTEM_PROMPT,)


def _declared_count(kind: str):
    def prompts():
        from features.ask import declared_count_language as module

        return (getattr(module, f"{kind}_SYSTEM"),)

    def formats():
        from features.ask import declared_count_language as module

        return (json_schema(getattr(module, f"{kind}_PURPOSE"), getattr(module, f"{kind}_SCHEMA")),)

    return prompts, formats


def _output_completion(kind: str, purpose: str):
    def prompts():
        from features.ask import output_completion as module

        return (getattr(module, f"{kind}_SYSTEM"),)

    def formats():
        from features.ask import output_completion as module

        return (json_schema(purpose, getattr(module, f"{kind}_SCHEMA")),)

    return prompts, formats


def _mean_precision_formats():
    from features.ask import mean_precision as module

    return tuple(json_schema("integer_mean_precision", schema)
                 for schema in (module.RESPONSE_SCHEMA, module.BINARY_RESPONSE_SCHEMA))


def _comparison_ratio_formats():
    from features.ask import comparison_ratio as module

    return tuple(json_schema("comparison_ratio", schema)
                 for schema in (module.SCHEMA, module.SCALAR_REFERENCE_SCHEMA))


def _list_membership_prompts():
    from features.ask import list_membership as module

    return (module.SYSTEM, module.SET_ABSENCE_SYSTEM)


def _state_lookup_prompts():
    from features.ask import state_lookup as module

    return (module.SYSTEM, module.NUMERIC_TEXT_SYSTEM)


def _projection_contract_prompts():
    from features.ask import projection_contract as module

    return (module.SYSTEM, module.VERBATIM_EXCERPT_SYSTEM)


def _projection_contract_formats():
    from features.ask import projection_contract as module

    # The index bound narrows to each query's projection count.
    return (json_schema("projection_contract", module.response_schema(1023)),)


def _analyze_prompts():
    from features.analyze.functions.llm_analysis import ANALYZE_SYSTEM_PROMPT
    from features.analyze.functions.shallow_analysis import SHALLOW_SYSTEM_PROMPT

    return (ANALYZE_SYSTEM_PROMPT, SHALLOW_SYSTEM_PROMPT)


def _analyze_formats():
    from features.analyze.functions.llm_analysis import ANALYZE_RESPONSE_FORMAT
    from features.analyze.functions.shallow_analysis import SHALLOW_RESPONSE_FORMAT

    return (ANALYZE_RESPONSE_FORMAT, SHALLOW_RESPONSE_FORMAT)


def _monitor_analyze_prompts():
    # Automatic monitor runs label every call of the analyze workflow monitor_analyze,
    # including its caching recommendations.
    return (*_analyze_prompts(), "You are a Readyset caching optimization expert.")


def _interactive_prompts():
    from features.interactive.service import get_interactive_mode_prompt

    return (get_interactive_mode_prompt(),)


def _help_prompts():
    from shared.cli.help_command import help_system_prompt

    return (help_system_prompt(windows=False), help_system_prompt(windows=True))


def _help_chat_prompts():
    from shared.api.routes.report import help_chat_system_prompt

    return (help_chat_system_prompt(),)


def _test_data_prompts():
    from features.cache.test_data_generator import TEST_DATA_SYSTEM_PROMPT

    return (TEST_DATA_SYSTEM_PROMPT,)


def _orm_prompts():
    from features.scan.service import ORM_CONVERSION_SYSTEM_PROMPT

    return (ORM_CONVERSION_SYSTEM_PROMPT,)


_HELP_LEGACY_PROMPT = (
    "You are a helpful assistant for RDST, a database performance analysis CLI tool.\n"
    "Answer the user's question based on the documentation provided. Be concise and "
    "practical.\nInclude command examples when relevant. If the question isn't covered "
    "in the docs, say so."
)
_DECLARED_REQUEST = _declared_count("REQUEST")
_DECLARED_BINDING = _declared_count("BINDING")
_OUTPUT_REQUEST = _output_completion("REQUEST", "output_request_routing")
_OUTPUT_BIND = _output_completion("BIND", "output_completion_routing")
_RELEASED_DEFAULT = (DEFAULT_SYSTEM_PROMPT,)

OPERATIONS: dict[str, Operation] = {
    # Ask
    "sql_generation": Operation(4000, schema_context=True, prompts=_sql_generation_prompts,
                                response_formats=_sql_generation_formats),
    "sql_generation_alternate": Operation(4000, prompts=_alternate_prompts,
                                          response_formats=lambda: (None,)),
    "sql_validation_repair": Operation(4000, legacy_prompts=_RELEASED_DEFAULT),
    "sql_refinement": Operation(2000, legacy_prompts=_RELEASED_DEFAULT),
    "sql_recovery": Operation(2000, legacy_prompts=_RELEASED_DEFAULT),
    "clarification": Operation(1600, schema_context=True, prompts=lambda: ("",)),
    "correction_intent_routing": Operation(800, response_formats=_correction_intent_formats),
    "example_questions": Operation(300, max_user_bytes=64 * 1024),
    "declared_entity_count_request": Operation(
        1200, prompts=_DECLARED_REQUEST[0], response_formats=_DECLARED_REQUEST[1]),
    "declared_entity_count_host_owner_binding": Operation(
        1600, prompts=_DECLARED_BINDING[0], response_formats=_DECLARED_BINDING[1]),
    "output_request_routing": Operation(
        1000, prompts=_OUTPUT_REQUEST[0], response_formats=_OUTPUT_REQUEST[1]),
    "output_completion_routing": Operation(
        1000, prompts=_OUTPUT_BIND[0], response_formats=_OUTPUT_BIND[1]),
    "endpoint_component_routing": Operation(2000),
    "endpoint_request_routing": Operation(1600),
    "endpoint_count_policy_routing": Operation(1600),
    "integer_mean_precision_routing": Operation(500, response_formats=_mean_precision_formats),
    "text_mean_precision_routing": Operation(700),
    "month_component_routing": Operation(500),
    "comparison_ratio_routing": Operation(700, response_formats=_comparison_ratio_formats),
    "list_membership_routing": Operation(1000, prompts=_list_membership_prompts),
    "state_lookup_routing": Operation(700, prompts=_state_lookup_prompts),
    "projection_contract_routing": Operation(700, prompts=_projection_contract_prompts,
                                             response_formats=_projection_contract_formats),
    "grouped_request_routing": Operation(700),
    "period_literal_routing": Operation(700),
    "named_entity_label_completion": Operation(1000),
    "request_entity_expansion": Operation(1000),
    "collection_request_routing": Operation(1500),
    "percentage_threshold_unit": Operation(1000),
    "percentage_threshold_request": Operation(1200),
    "annual_request_routing": Operation(1800),
    "calendar_day_routing": Operation(900),
    "name_format_routing": Operation(700),
    "metric_source_routing": Operation(1200),
    "projection_order_routing": Operation(700),
    "outer_rounding_routing": Operation(600),
    "fraction_precision_routing": Operation(700),
    "scaled_ratio_routing": Operation(900),
    "ranked_union_routing": Operation(900),
    "percentage_roles_routing": Operation(1800),
    "matched_percentage_request_routing": Operation(1600),
    "null_extremum_routing": Operation(700),
    # Analyze and monitoring
    "analyze_query": Operation(8192, schema_context=True, prompts=_analyze_prompts,
                               response_formats=_analyze_formats),
    "monitor_analyze": Operation(8192, schema_context=True, prompts=_monitor_analyze_prompts,
                                 response_formats=lambda: (*_analyze_formats(), None)),
    "analyze_cache": Operation(1500),
    "analyze_rewrite": Operation(1500),
    "analyze_index": Operation(1500),
    "monitor_report": Operation(8192),
    "monitor_explain": Operation(4096),
    "interactive_answer": Operation(2000, max_user_bytes=256 * 1024, multi_turn=True,
                                    prompts=_interactive_prompts,
                                    response_formats=lambda: (None,)),
    # Audit, fleet and queries
    "audit_capture_analysis": Operation(6144, legacy_prompts=_RELEASED_DEFAULT),
    "audit_health": Operation(4096, legacy_prompts=_RELEASED_DEFAULT),
    "audit_single_target_insights": Operation(4096, legacy_prompts=_RELEASED_DEFAULT),
    "fleet_insights": Operation(4096, legacy_prompts=_RELEASED_DEFAULT),
    "query_registry_analysis": Operation(2048, legacy_prompts=_RELEASED_DEFAULT),
    # Schema, scan, guard, cache, init, help
    "schema_annotation": Operation(4096),
    "schema_table_annotation": Operation(500),
    "schema_column_annotation": Operation(200),
    "schema_enum_annotation": Operation(800),
    "schema_guided_analysis": Operation(4096),
    "orm_conversion": Operation(2000, schema_context=True, prompts=_orm_prompts,
                                response_formats=lambda: (None,)),
    "guard_rule_derivation": Operation(2000),
    "test_data_generation": Operation(800, prompts=_test_data_prompts,
                                      response_formats=lambda: (None,)),
    "init_connectivity_check": Operation(8, max_user_bytes=256),
    "help_answer": Operation(1000, max_user_bytes=16 * 1024, legacy_max_user_bytes=64 * 1024,
                             prompts=_help_prompts, response_formats=lambda: (None,),
                             legacy_prompts=(_HELP_LEGACY_PROMPT,)),
    "help_chat": Operation(1000, max_user_bytes=64 * 1024, multi_turn=True,
                           prompts=_help_chat_prompts, response_formats=lambda: (None,)),
}

# Call sites whose operation is a parameter. Each operation they serve lists its prompts
# and response formats above.
DYNAMIC_CALL_SITES = {
    "features/ask/sql_generation.py": ("sql_generation",),
    "features/ask/declared_count.py": (
        "declared_entity_count_request", "declared_entity_count_host_owner_binding"),
    "features/ask/output_completion.py": ("output_request_routing", "output_completion_routing"),
    "features/ask/endpoint_component.py": (),
    "shared/llm_manager/llm_manager.py": (),
}

# Call sites that never reach hosted inference: a direct Claude key check, the tool-using
# Ask agent (hosted inference has no tool use), and the demo workflow runtime.
NON_HOSTED_CALL_SITES = frozenset({
    "shared/anthropic_env.py",
    "features/ask/engine/ask3/agent.py",
    "shared/workflow_manager_runtime.py",
})
