"""Primary-key facts must come from complete catalog declarations."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
import sqlglot
from sqlglot import exp

from features.ask.calendar_day import structural_schema
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases.schema import (
    _build_schema_info_from_semantic,
    load_schema,
    select_semantic_schema_serialization,
)
from features.ask.matched_percentage import plan_matched_percentage
from features.schema.semantic_models import (
    ColumnAnnotation, IndexAnnotation, SemanticLayer, TableAnnotation,
)


def layer(indexes):
    return SemanticLayer(target='fixture', tables={
        'members': TableAnnotation(name='members', columns={
            n: ColumnAnnotation(name=n, data_type=t)
            for n, t in {'id':'int', 'member_key':'int', 'tenant':'int', 'status':'text'}.items()
        }, indexes=indexes),
        'visits': TableAnnotation(name='visits', columns={
            n: ColumnAnnotation(name=n, data_type='int')
            for n in ('visit_key','member_key')
        }, indexes={'pk': IndexAnnotation(name='pk', columns=['visit_key'], is_primary=True, is_unique=True)})
    })


def primary(columns):
    return IndexAnnotation(name='catalog_primary', columns=columns, is_primary=True, is_unique=True)


def keys(schema):
    return {n for n,c in schema.tables['members'].columns.items() if c.is_primary_key}


@pytest.mark.parametrize('dialect', ['mysql','postgresql'])
@pytest.mark.parametrize('indexes,expected', [
    ({'pk':primary(['member_key'])}, {'member_key'}),
    ({'pk':primary(['id'])}, {'id'}),
    ({'pk':primary(['tenant','member_key'])}, {'tenant','member_key'}),
    ({}, set()),
    ({'unique':IndexAnnotation(name='unique',columns=['id'],is_unique=True)}, set()),
    ({'pk':primary(['member_key','missing'])}, set()),
    ({'pk':primary([])}, set()),
    ({'pk':primary(['member_key','member_key'])}, set()),
    ({'pk':primary('member_key')}, set()),
    ({'pk':primary([['member_key']])}, set()),
    ({'first':primary(['member_key']),'second':primary(['id'])}, set()),
    ({'pk':primary(['MEMBER_KEY'])}, set()),
])
def test_complete_catalog_primary_membership(dialect, indexes, expected):
    source=layer(indexes);before=deepcopy(source.to_dict())
    schema=_build_schema_info_from_semantic(source,'fixture',dialect)
    assert keys(schema)==expected
    assert source.to_dict()==before
    assert {n for n,c in structural_schema(schema)['tables']['members']['columns'].items() if c['is_primary_key']}==expected


def test_quoted_postgres_key_spelling_is_preserved():
    source=layer({'pk':primary(['MemberKey'])})
    source.tables['members'].columns['MemberKey']=source.tables['members'].columns.pop('member_key')
    source.tables['members'].columns['MemberKey'].name='MemberKey'
    assert keys(_build_schema_info_from_semantic(source,'fixture','postgresql'))=={'MemberKey'}


@pytest.mark.parametrize('indexes,key,eligible', [
    ({'pk':primary(['member_key'])},'member_key',True),
    ({},'id',False),
    ({'pk':primary(['tenant','member_key'])},'member_key',False),
    ({'unique':IndexAnnotation(name='unique',columns=['id'],is_unique=True)},'id',False),
])
def test_percentage_planner_requires_declared_singleton_key(indexes,key,eligible):
    schema=_build_schema_info_from_semantic(layer(indexes),'fixture','mysql')
    sql=f"SELECT AVG(CASE WHEN m.status='active' THEN 100.0 ELSE 0 END) FROM visits v LEFT JOIN members m ON v.member_key=m.{key}"
    plan=plan_matched_percentage(sql,'mysql',schema)
    assert (plan is not None)==eligible
    if plan:
        denominator = sqlglot.parse_one(plan.candidate_sql, read='mysql').find(exp.Count).this
        assert (denominator.table, denominator.name) == ('m', key)


@pytest.mark.parametrize('dialect', ['mysql','postgresql'])
def test_canonical_schema_loading_keeps_generation_text_and_truthful_keys(dialect):
    source=layer({'pk':primary(['member_key'])})
    expected=select_semantic_schema_serialization(source).formatted
    manager=Mock();manager.exists.return_value=True;manager.load.return_value=source
    ctx=Ask3Context(question='How many members?',target='fixture',db_type=dialect)
    result=load_schema(ctx,Mock(),semantic_manager=manager)
    assert result is ctx and keys(ctx.schema_info)=={'member_key'}
    assert ctx.schema_formatted==expected
    manager.save.assert_not_called()
