"""API routes for guard management.

Guards are reusable safety policies stored in ~/.rdst/guards/*.yaml. All
endpoints are plain REST; the only slow paths are intent derivation (LLM)
and checks that need EXPLAIN (database), both run in worker threads.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from features.guard import (
    GuardConfig,
    GuardExistsError,
    GuardManager,
    GuardNotFoundError,
    InvalidGuardNameError,
    check_query,
    derive_rules_from_intent,
)
from features.guard.config import (
    GuardsConfig,
    LimitsConfig,
    MaskingConfig,
    RestrictionsConfig,
)
from shared.api.target_guard import ensure_target_password

router = APIRouter(prefix="/guards", tags=["guards"])


class GuardRestrictionsModel(BaseModel):
    denied_columns: Optional[list[str]] = None
    allowed_tables: Optional[list[str]] = None
    required_filters: Optional[dict[str, list[str]]] = None


class GuardRulesModel(BaseModel):
    require_where: bool = False
    require_limit: bool = False
    no_select_star: bool = False
    max_tables: Optional[int] = None
    cost_limit: Optional[int] = None
    max_estimated_rows: Optional[int] = None


class GuardLimitsModel(BaseModel):
    max_rows: int = 1000
    timeout_seconds: int = 30


class GuardDetail(BaseModel):
    name: str
    description: str = ""
    intent: str = ""
    derived: bool = False
    created_at: Optional[str] = None
    masking: dict[str, str] = Field(default_factory=dict)
    restrictions: GuardRestrictionsModel = Field(default_factory=GuardRestrictionsModel)
    guards: GuardRulesModel = Field(default_factory=GuardRulesModel)
    limits: GuardLimitsModel = Field(default_factory=GuardLimitsModel)


class GuardSummary(BaseModel):
    name: str
    description: str = ""
    derived: bool = False
    mask_count: int = 0
    rules: list[str] = Field(default_factory=list)
    max_rows: int = 1000
    created_at: Optional[str] = None


class GuardListResponse(BaseModel):
    guards: list[GuardSummary]
    count: int


class GuardWriteResponse(BaseModel):
    success: bool
    name: str
    path: Optional[str] = None


class GuardDeriveRequest(BaseModel):
    name: str
    intent: str
    schema_context: Optional[str] = None


class GuardCheckRequest(BaseModel):
    sql: str
    target: Optional[str] = None


class GuardCheckResult(BaseModel):
    passed: bool
    level: str
    guard_name: str
    message: str
    suggestion: Optional[str] = None


class GuardCheckResponse(BaseModel):
    guard: str
    sql: str
    passed: bool
    results: list[GuardCheckResult]


def _config_to_detail(config: GuardConfig) -> GuardDetail:
    return GuardDetail(
        name=config.name,
        description=config.description,
        intent=config.intent,
        derived=config.derived,
        created_at=config.created_at,
        masking=dict(config.masking.patterns),
        restrictions=GuardRestrictionsModel(
            denied_columns=config.restrictions.denied_columns,
            allowed_tables=config.restrictions.allowed_tables,
            required_filters=config.restrictions.required_filters,
        ),
        guards=GuardRulesModel(
            require_where=config.guards.require_where,
            require_limit=config.guards.require_limit,
            no_select_star=config.guards.no_select_star,
            max_tables=config.guards.max_tables,
            cost_limit=config.guards.cost_limit,
            max_estimated_rows=config.guards.max_estimated_rows,
        ),
        limits=GuardLimitsModel(
            max_rows=config.limits.max_rows,
            timeout_seconds=config.limits.timeout_seconds,
        ),
    )


def _detail_to_config(detail: GuardDetail) -> GuardConfig:
    config = GuardConfig(
        name=detail.name,
        description=detail.description,
        intent=detail.intent,
        derived=detail.derived,
        masking=MaskingConfig(patterns=dict(detail.masking)),
        restrictions=RestrictionsConfig(
            denied_columns=detail.restrictions.denied_columns,
            allowed_tables=detail.restrictions.allowed_tables,
            required_filters=detail.restrictions.required_filters,
        ),
        guards=GuardsConfig(
            require_where=detail.guards.require_where,
            require_limit=detail.guards.require_limit,
            no_select_star=detail.guards.no_select_star,
            max_tables=detail.guards.max_tables,
            cost_limit=detail.guards.cost_limit,
            max_estimated_rows=detail.guards.max_estimated_rows,
        ),
        limits=LimitsConfig(
            max_rows=detail.limits.max_rows,
            timeout_seconds=detail.limits.timeout_seconds,
        ),
    )
    if detail.created_at:
        config.created_at = detail.created_at
    return config


def _config_to_summary(config: GuardConfig) -> GuardSummary:
    rules: list[str] = []
    if config.guards.require_where:
        rules.append("where")
    if config.guards.require_limit:
        rules.append("limit")
    if config.restrictions.required_filters:
        rules.append("filters")
    if config.guards.max_estimated_rows:
        rules.append("est_rows")
    if config.guards.max_tables:
        rules.append(f"tbl:{config.guards.max_tables}")
    if config.guards.no_select_star:
        rules.append("no_select*")
    if config.guards.cost_limit:
        rules.append("cost")

    return GuardSummary(
        name=config.name,
        description=config.description,
        derived=config.derived,
        mask_count=len(config.masking.patterns),
        rules=rules,
        max_rows=config.limits.max_rows,
        created_at=config.created_at,
    )


@router.get("")
async def list_guards() -> GuardListResponse:
    """List all guards with summary info."""
    manager = GuardManager()
    summaries = [_config_to_summary(cfg) for cfg in manager.list_configs()]
    return GuardListResponse(guards=summaries, count=len(summaries))


@router.get("/{name}")
async def get_guard(name: str) -> GuardDetail:
    """Get full guard configuration."""
    try:
        config = GuardManager().get(name)
    except GuardNotFoundError:
        raise HTTPException(status_code=404, detail=f"Guard '{name}' not found")
    return _config_to_detail(config)


@router.post("")
async def create_guard(detail: GuardDetail) -> GuardWriteResponse:
    """Create a guard from an explicit configuration.

    Intent-derived configs from POST /guards/derive are saved through here
    after the user reviews them.
    """
    config = _detail_to_config(detail)
    try:
        path = GuardManager().create(config)
    except InvalidGuardNameError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except GuardExistsError:
        raise HTTPException(
            status_code=409, detail=f"Guard '{detail.name}' already exists"
        )
    return GuardWriteResponse(success=True, name=detail.name, path=str(path))


@router.put("/{name}")
async def update_guard(name: str, detail: GuardDetail) -> GuardWriteResponse:
    """Replace a guard's configuration. The name is immutable."""
    if detail.name != name:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot change guard name. Got '{detail.name}', expected '{name}'",
        )
    try:
        path = GuardManager().update(_detail_to_config(detail))
    except GuardNotFoundError:
        raise HTTPException(status_code=404, detail=f"Guard '{name}' not found")
    return GuardWriteResponse(success=True, name=name, path=str(path))


@router.delete("/{name}")
async def delete_guard(name: str) -> GuardWriteResponse:
    """Delete a guard."""
    try:
        GuardManager().delete(name)
    except GuardNotFoundError:
        raise HTTPException(status_code=404, detail=f"Guard '{name}' not found")
    return GuardWriteResponse(success=True, name=name)


@router.post("/derive")
async def derive_guard(request: GuardDeriveRequest) -> GuardDetail:
    """Derive guard rules from a natural-language intent (preview only).

    Returns the proposed configuration without saving it; the client reviews
    or edits the rules and saves via POST /guards.
    """
    try:
        config = await asyncio.to_thread(
            derive_rules_from_intent,
            intent=request.intent,
            name=request.name,
            schema_context=request.schema_context,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Failed to derive rules: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")
    return _config_to_detail(config)


@router.post("/{name}/check")
async def check_guard(name: str, request: GuardCheckRequest) -> GuardCheckResponse:
    """Validate SQL against a guard.

    With a target, EXPLAIN-backed checks (cost_limit, max_estimated_rows)
    run against the database; without one they are skipped, matching the CLI.
    """
    try:
        config = GuardManager().get(name)
    except GuardNotFoundError:
        raise HTTPException(status_code=404, detail=f"Guard '{name}' not found")

    target_name = None
    target_config = None
    if request.target:
        guard = ensure_target_password(request.target)
        target_name = guard.target_name
        target_config = guard.target_config

    results = await asyncio.to_thread(
        check_query,
        request.sql,
        config,
        target_name=target_name,
        target_config=target_config,
    )

    passed = all(r.passed or r.level == "warn" for r in results)
    return GuardCheckResponse(
        guard=name,
        sql=request.sql,
        passed=passed,
        results=[
            GuardCheckResult(
                passed=r.passed,
                level=r.level,
                guard_name=r.guard_name,
                message=r.message,
                suggestion=r.suggestion,
            )
            for r in results
        ],
    )
