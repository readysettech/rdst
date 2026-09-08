"""Apply one declared-entity count correction through a fresh bounded statement.

Uses ordinary Ask read semantics. Only a fresh callback result can be adopted;
saved proof rows are never an input to this function.
"""
from copy import deepcopy
from decimal import Decimal
import json
import time

from .declared_count_language import validate_binding
from .declared_count_wrapper import build_wrapper, decode_row
from .declared_count_proof import BOUNDS, check_count_row
from hashlib import sha256
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.sql_validation import validate_sql_for_ask, validate_resolved_columns_against_schema
from features.ask.value_probe import _create_bounded_probe_executor

VERSION = 'declared-count-fresh-native-v1'


def need(condition, reason):
    if not condition: raise ValueError(reason)


def typed_rows_bytes(rows):
    def retain(value):
        if type(value) is Decimal:
            need(value.is_finite(), 'nonfinite-native-decimal')
            return {'native_type': 'Decimal', 'value': str(value)}
        if value is None or type(value) in (int, bool, str): return value
        if type(value) in (list, tuple): return [retain(v) for v in value]
        raise ValueError('unsupported-native-type:'+type(value).__name__)
    return len(json.dumps(retain(rows), ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode())


def validate_exact(sql, schema, dialect):
    checked = validate_sql_for_ask(sql, enforce_result_limit=False)
    need(checked.get('is_valid') is True, 'sql-preflight-failed')
    need(checked.get('validated_sql', sql) == sql, 'sql-preflight-changed-frozen-bytes')
    columns = {name:list(table['columns']) for name,table in schema['tables'].items()}
    resolved = validate_resolved_columns_against_schema(sql, columns, dialect)
    need(resolved.get('is_valid') is True, 'column-resolution-failed')


def apply_count_repair(ctx, schema, request, binding, *, execution_id,
                       snapshot_correction, restore_correction, reserve=None, db_executor=None):
    """One bounded fresh proof/candidate call; no accepted saved-proof argument.

    ``reserve`` records the exact planned invocation before it can enter the
    executor. An optional caller recorder may persist that reservation. The
    callback is the ordinary Ask executor, including its normal read semantics.
    Correction state is restored on every failed check.
    """
    original = snapshot_correction(ctx)
    diagnostics = dict(version=VERSION, status='unchanged', may_adopt=False,
        reserved_calls=0, data_statement_budget=1, observed_result_available=False,
        ordinary_read_semantics=True, snapshot_certification_claimed=False)
    committed = False
    try:
        result = original['execution_result']
        need(result is not None and result.error is None and result.truncated is False,
             'complete-original-unavailable')
        need(type(result.columns) is list and len(result.columns)==1 and type(result.columns[0]) is str,
             'original-column-shape')
        need(type(result.rows) is list and len(result.rows)==1 and type(result.rows[0]) is list
             and len(result.rows[0])==1, 'original-row-shape')
        need(type(result.row_count) is int and result.row_count==1, 'original-row-count-conflict')
        need(type(result.rows[0][0]) in (int,Decimal), 'original-native-type')
        need(type(result.rows[0][0]) is not Decimal or result.rows[0][0].is_finite(), 'nonfinite-original')
        need(not getattr(ctx,'provided_context',None) and not getattr(ctx,'conversation_context',None),
             'additional-context-not-qualified')
        question = getattr(ctx,'refined_question',None) or ctx.question
        wrapper = build_wrapper(original['sql'], schema, ctx.db_type)
        need(wrapper['supported'] is True, wrapper.get('reason','unsupported-shape'))
        plan = wrapper['plan']
        semantic = validate_binding(question, request, binding, plan['catalog'])
        diagnostics['semantic_check'] = semantic
        need(semantic['valid'] is True and semantic['eligible'] is True, 'semantic-binding-ineligible')
        query_hashes = dict(original=plan['original_sha256'], candidate=plan['candidate_sha256'],
                            wrapper=wrapper['wrapper_sha256'], catalog=sha256(json.dumps(
                                plan['catalog'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest())
        for sql in (plan['candidate_sql'], wrapper['wrapper_sql']):
            validate_exact(sql, schema, plan['dialect'])
        diagnostics.update(execution_id=execution_id, query_hashes=query_hashes,
                           proof_sql=wrapper['wrapper_sql'], candidate_sql=plan['candidate_sql'])
        reservation = dict(version=VERSION, execution_id=execution_id, query_hashes=query_hashes,
                           declared_parent_role_ids=[r['id'] for r in plan['catalog']['parent_roles']],
                           present_parent_role_ids=plan['catalog']['present_parent_role_ids'], bounds=dict(BOUNDS))
        diagnostics['reservation'] = deepcopy(reservation)
        ctx.count_native = diagnostics
        if reserve is not None:
            need(callable(reserve), 'invalid-reservation-recorder')
            reserve(deepcopy(reservation))
        diagnostics['reserved_calls'] = 1
        bounded = _create_bounded_probe_executor(ctx, db_executor, max_calls=1,
            timeout_seconds=2, max_rows=2, diagnostic_attribute='count_native_probe_diagnostics', version=VERSION)
        tick = time.perf_counter_ns()
        native = bounded(wrapper['wrapper_sql'], ctx.target_config)
        elapsed = time.perf_counter_ns()-tick
        diagnostics.update(elapsed_nanoseconds=elapsed, native_result=deepcopy(native),
                           observed_result_available=True)
        need(type(elapsed) is int and 0 <= elapsed <= BOUNDS['timeout_nanoseconds'], 'native-deadline-exceeded')
        need(type(native) is dict and native.get('success') is True and native.get('error') is None,
             'native-execution-failed')
        # The existing completeness wrappers use the same truthy flag
        # veto. Missing flags retain the ordinary executor's successful-fetch
        # semantics; present contradictory flags can never certify completeness.
        need(not native.get('truncated') and not native.get('timed_out') and not native.get('result_too_large'),
             'native-result-incomplete')
        need('complete' not in native or native['complete'] is True, 'native-complete-flag-conflict')
        need('row_count' not in native or type(native['row_count']) is int and native['row_count']==1,
             'native-row-count-conflict')
        row = decode_row(wrapper, native.get('columns'), native.get('rows'))
        byte_count = typed_rows_bytes(native['rows'])
        need(0 < byte_count <= BOUNDS['max_result_bytes'], 'native-byte-bound')
        scalar = check_count_row(row,
            [r['id'] for r in plan['catalog']['parent_roles']], result.rows[0][0])
        diagnostics.update(native_proof_row=deepcopy(row), native_value_bytes=byte_count)
        candidate = deepcopy(original)
        candidate['sql'] = plan['candidate_sql']
        candidate['execution_result'] = ExecutionResult(columns=deepcopy(result.columns),
            rows=[[scalar]], row_count=1,
            execution_time_ms=elapsed/1_000_000, error=None, truncated=False)
        restore_correction(ctx,candidate)
        diagnostics.update(status='normalized', may_adopt=True,
            acceptance_basis='fresh-complete-combined-statement-and-exact-native-checks',
            executed_sql=wrapper['wrapper_sql'], logical_candidate_invocations=1)
        committed = True
    except Exception as exc:
        diagnostics.update(status='reverted', reason=str(exc), error_kind=type(exc).__name__)
    finally:
        if not committed: restore_correction(ctx,original)
        ctx.count_native = diagnostics
    return ctx
