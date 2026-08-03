"""API routes for semantic layer management."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from features.schema.annotate_service import AnnotateService
from features.schema.events import (
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
    AnnotateProgressEvent,
    AnnotateStartedEvent,
    AnnotateTableCompleteEvent,
    SchemaCompleteEvent,
    SchemaErrorEvent,
    SchemaEvent,
    SchemaStatusEvent,
)
from features.schema.models import (
    SchemaCustomType,
    SchemaDeleteResult,
    SchemaDetails,
    SchemaExportResult,
    SchemaExtension,
    SchemaInitOptions,
    SchemaInitResult,
    SchemaMetric,
    SchemaStatus,
    SchemaTable,
    SchemaTableColumn,
    SchemaTableRelationship,
    SchemaTargetList,
    SchemaTargetSummary,
    SchemaTerminology,
    SchemaUpdateResult,
)
from features.schema.service import SchemaService
from shared.api.target_guard import TargetGuard, require_target, require_target_body
from shared.run_registry import run_registry

router = APIRouter(tags=["semantic-layer"])

# Shared process-local registry; tests replace it with an isolated instance.
_registry = run_registry


class SchemaStatusResponse(BaseModel):
    target: str
    exists: bool
    tables: int
    columns: int
    relationships: int
    terminology: int
    updated_at: Optional[str] = None
    profiled_tables: int = 0
    profiled_at: Optional[str] = None


class SchemaTableColumnResponse(BaseModel):
    name: str
    data_type: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    is_pii: bool = False
    enum_values: Optional[dict[str, str]] = None
    null_fraction: Optional[float] = None
    distinct_count: Optional[int] = None


class SchemaTableRelationshipResponse(BaseModel):
    target_table: str
    relationship_type: str
    join_pattern: str


class SchemaTableResponse(BaseModel):
    name: str
    description: Optional[str] = None
    business_context: Optional[str] = None
    row_estimate: Optional[str] = None
    columns: list[SchemaTableColumnResponse]
    relationships: list[SchemaTableRelationshipResponse]


class SchemaTerminologyResponse(BaseModel):
    term: str
    definition: str
    sql_pattern: str
    synonyms: list[str]


class SchemaMetricResponse(BaseModel):
    name: str
    definition: str
    sql: str


class SchemaExtensionResponse(BaseModel):
    name: str
    version: str
    description: Optional[str] = None
    types_provided: list[str]


class SchemaCustomTypeResponse(BaseModel):
    name: str
    type_category: str
    base_type: Optional[str] = None
    enum_values: Optional[list[str]] = None
    description: Optional[str] = None


class SchemaDetailsResponse(BaseModel):
    target: str
    tables: list[SchemaTableResponse]
    terminology: list[SchemaTerminologyResponse]
    extensions: list[SchemaExtensionResponse]
    custom_types: list[SchemaCustomTypeResponse]
    metrics: list[SchemaMetricResponse]


class SchemaTargetSummaryResponse(BaseModel):
    name: str
    tables: int
    terminology: int
    updated_at: Optional[str] = None


class SchemaTargetListResponse(BaseModel):
    targets: list[SchemaTargetSummaryResponse]


class SchemaInitRequest(BaseModel):
    target: str
    enum_threshold: int = 20
    force: bool = False
    sample_enums: bool = True


class SchemaInitResponse(BaseModel):
    success: bool
    target: str
    tables: int
    columns: int
    relationships: int
    enum_columns: list[str]
    path: Optional[str] = None
    error: Optional[str] = None
    code: Optional[str] = None
    category: Optional[str] = None


class SchemaExportResponse(BaseModel):
    success: bool
    format: str
    content: str
    error: Optional[str] = None


class SchemaDeleteResponse(BaseModel):
    success: bool
    target: str
    error: Optional[str] = None


class SchemaUpdateResponse(BaseModel):
    success: bool
    message: str
    error: Optional[str] = None


class AddTableRequest(BaseModel):
    target: str
    table_name: str
    description: str
    business_context: str = ""
    row_estimate: str = ""


class AddColumnRequest(BaseModel):
    target: str
    table_name: str
    column_name: str
    description: str
    data_type: Optional[str] = None
    unit: Optional[str] = None
    is_pii: bool = False


class AddEnumRequest(BaseModel):
    target: str
    table_name: str
    column_name: str
    enum_values: dict[str, str]


class AddTerminologyRequest(BaseModel):
    target: str
    term: str
    definition: str
    sql_pattern: str
    synonyms: Optional[list[str]] = None


class AddRelationshipRequest(BaseModel):
    target: str
    source_table: str
    target_table: str
    join_pattern: str
    relationship_type: str = "one_to_many"


class AddMetricRequest(BaseModel):
    target: str
    name: str
    definition: str
    sql: str
    unit: str = ""


class AnnotateRequest(BaseModel):
    target: str
    table_name: Optional[str] = None
    sample_rows: int = 5


class AnnotationRunStartResponse(BaseModel):
    run_id: str
    reused: bool = False


def _status_to_response(status: SchemaStatus) -> SchemaStatusResponse:
    return SchemaStatusResponse(
        target=status.target,
        exists=status.exists,
        tables=status.tables,
        columns=status.columns,
        relationships=status.relationships,
        terminology=status.terminology,
        updated_at=status.updated_at,
        profiled_tables=status.profiled_tables,
        profiled_at=status.profiled_at,
    )


def _details_to_response(details: SchemaDetails) -> SchemaDetailsResponse:
    tables = [
        SchemaTableResponse(
            name=t.name,
            description=t.description,
            business_context=t.business_context,
            row_estimate=t.row_estimate,
            columns=[
                SchemaTableColumnResponse(
                    name=c.name,
                    data_type=c.data_type,
                    description=c.description,
                    unit=c.unit,
                    is_pii=c.is_pii,
                    enum_values=c.enum_values,
                    null_fraction=c.null_fraction,
                    distinct_count=c.distinct_count,
                )
                for c in t.columns
            ],
            relationships=[
                SchemaTableRelationshipResponse(
                    target_table=r.target_table,
                    relationship_type=r.relationship_type,
                    join_pattern=r.join_pattern,
                )
                for r in t.relationships
            ],
        )
        for t in details.tables
    ]

    terminology = [
        SchemaTerminologyResponse(
            term=t.term,
            definition=t.definition,
            sql_pattern=t.sql_pattern,
            synonyms=t.synonyms,
        )
        for t in details.terminology
    ]

    metrics = [
        SchemaMetricResponse(
            name=m.name,
            definition=m.definition,
            sql=m.sql,
        )
        for m in details.metrics
    ]

    extensions = [
        SchemaExtensionResponse(
            name=e.name,
            version=e.version,
            description=e.description,
            types_provided=e.types_provided,
        )
        for e in details.extensions
    ]

    custom_types = [
        SchemaCustomTypeResponse(
            name=ct.name,
            type_category=ct.type_category,
            base_type=ct.base_type,
            enum_values=ct.enum_values,
            description=ct.description,
        )
        for ct in details.custom_types
    ]

    return SchemaDetailsResponse(
        target=details.target,
        tables=tables,
        terminology=terminology,
        extensions=extensions,
        custom_types=custom_types,
        metrics=metrics,
    )


def _target_list_to_response(target_list: SchemaTargetList) -> SchemaTargetListResponse:
    return SchemaTargetListResponse(
        targets=[
            SchemaTargetSummaryResponse(
                name=t.name,
                tables=t.tables,
                terminology=t.terminology,
                updated_at=t.updated_at,
            )
            for t in target_list.targets
        ]
    )


def _init_result_to_response(result: SchemaInitResult) -> SchemaInitResponse:
    return SchemaInitResponse(
        success=result.success,
        target=result.target,
        tables=result.tables,
        columns=result.columns,
        relationships=result.relationships,
        enum_columns=result.enum_columns,
        path=result.path,
        error=result.error,
    )


def _export_result_to_response(result: SchemaExportResult) -> SchemaExportResponse:
    return SchemaExportResponse(
        success=result.success,
        format=result.format,
        content=result.content,
        error=result.error,
    )


def _delete_result_to_response(result: SchemaDeleteResult) -> SchemaDeleteResponse:
    return SchemaDeleteResponse(
        success=result.success,
        target=result.target,
        error=result.error,
    )


def _update_result_to_response(result: SchemaUpdateResult) -> SchemaUpdateResponse:
    return SchemaUpdateResponse(
        success=result.success,
        message=result.message,
        error=result.error,
    )


@router.get("/semantic-layer/status")
async def get_schema_status(
    guard: TargetGuard = Depends(require_target),
) -> SchemaStatusResponse:
    """Get semantic layer status for a target."""
    service = SchemaService()
    status = service.get_status(guard.target_name)
    return _status_to_response(status)


@router.get("/semantic-layer")
async def get_schema(
    guard: TargetGuard = Depends(require_target),
    table: Optional[str] = Query(None, description="Specific table to load"),
) -> SchemaDetailsResponse:
    """Get full semantic layer details or a single table."""
    service = SchemaService()
    details = service.get_schema(guard.target_name, table)

    if details is None:
        from features.schema.semantic_layer import SemanticLayerManager

        if table and SemanticLayerManager().exists(guard.target_name):
            detail_msg = f"Table '{table}' not found in semantic layer for '{guard.target_name}'"
        else:
            detail_msg = f"No semantic layer found for target '{guard.target_name}'"
        raise HTTPException(
            status_code=404,
            detail=detail_msg,
        )

    return _details_to_response(details)


@router.get("/semantic-layer/targets")
async def list_schema_targets() -> SchemaTargetListResponse:
    """List all targets with semantic layers."""
    service = SchemaService()
    target_list = service.list_targets()
    return _target_list_to_response(target_list)


class SchemaRefreshRequest(BaseModel):
    target: Optional[str] = None


class SchemaProfileRequest(BaseModel):
    target: Optional[str] = None
    table: Optional[str] = None


class SchemaOperationResponse(BaseModel):
    ok: bool
    message: str
    data: Optional[dict[str, Any]] = None
    code: Optional[str] = None
    category: Optional[str] = None


@router.post("/semantic-layer/refresh")
async def refresh_schema(
    request: SchemaRefreshRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaOperationResponse:
    """Re-introspect the database and merge structural changes, preserving annotations."""
    import asyncio

    service = SchemaService()
    result = await asyncio.to_thread(
        service.refresh, guard.target_name, guard.target_config
    )
    from shared.api.ssh_errors import connectivity_error_payload

    failure = (
        connectivity_error_payload(
            RuntimeError(result.get("message", "")),
            guard.target_name,
            guard.target_config,
        )
        if not result.get("ok", False)
        else None
    )
    return SchemaOperationResponse(
        ok=result.get("ok", False),
        message=failure["message"] if failure else result.get("message", ""),
        data=result.get("data"),
        code=failure["category"] if failure else None,
        category=failure["category"] if failure else None,
    )


@router.post("/semantic-layer/profile")
async def profile_schema(
    request: SchemaProfileRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaOperationResponse:
    """Profile column data distributions into the semantic layer."""
    import asyncio

    service = SchemaService()
    result = await asyncio.to_thread(
        service.profile, guard.target_name, guard.target_config, request.table
    )
    from shared.api.ssh_errors import connectivity_error_payload

    failure = (
        connectivity_error_payload(
            RuntimeError(result.get("message", "")),
            guard.target_name,
            guard.target_config,
        )
        if not result.get("ok", False)
        else None
    )
    return SchemaOperationResponse(
        ok=result.get("ok", False),
        message=failure["message"] if failure else result.get("message", ""),
        data=result.get("data"),
        code=failure["category"] if failure else None,
        category=failure["category"] if failure else None,
    )


@router.post("/semantic-layer/init")
async def init_schema(
    request: SchemaInitRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaInitResponse:
    """Initialize semantic layer by introspecting database schema."""
    service = SchemaService()
    options = SchemaInitOptions(
        enum_threshold=request.enum_threshold,
        force=request.force,
        sample_enums=request.sample_enums,
    )

    init_result = None
    error_message = None

    async for event in service.init_events(
        guard.target_name, guard.target_config, options
    ):
        if isinstance(event, SchemaCompleteEvent) and event.init_result:
            init_result = event.init_result
        elif isinstance(event, SchemaErrorEvent):
            error_message = event.message

    if init_result is not None:
        response = _init_result_to_response(init_result)
        if not response.success and response.error:
            from shared.api.ssh_errors import connectivity_error_payload

            failure = connectivity_error_payload(
                RuntimeError(response.error),
                guard.target_name,
                guard.target_config,
            )
            if failure:
                response.error = failure["message"]
                response.code = failure["category"]
                response.category = failure["category"]
        return response

    from shared.api.ssh_errors import connectivity_error_payload

    failure = connectivity_error_payload(
        RuntimeError(error_message or "Schema initialization failed"),
        guard.target_name,
        guard.target_config,
    )
    return SchemaInitResponse(
        success=False,
        target=guard.target_name,
        tables=0,
        columns=0,
        relationships=0,
        enum_columns=[],
        error=(
            failure["message"]
            if failure
            else error_message or "Schema initialization failed"
        ),
        code=failure["category"] if failure else None,
        category=failure["category"] if failure else None,
    )


def _schema_event_to_sse(event: SchemaEvent) -> dict:
    """Convert schema service events to SSE payloads."""
    if isinstance(event, SchemaStatusEvent):
        return {
            "event": "status",
            "data": json.dumps(
                {
                    "operation": event.operation,
                    "message": event.message,
                }
            ),
        }
    if isinstance(event, SchemaCompleteEvent):
        payload = {
            "operation": event.operation,
            "success": event.success,
        }
        if event.init_result:
            payload["init_result"] = {
                "success": event.init_result.success,
                "target": event.init_result.target,
                "tables": event.init_result.tables,
                "columns": event.init_result.columns,
                "relationships": event.init_result.relationships,
                "enum_columns": event.init_result.enum_columns,
                "path": event.init_result.path,
                "error": event.init_result.error,
            }
        return {"event": "complete", "data": json.dumps(payload)}
    if isinstance(event, SchemaErrorEvent):
        return {
            "event": "error",
            "data": json.dumps(
                {
                    "operation": event.operation,
                    "message": event.message,
                }
            ),
        }

    return {
        "event": "unknown",
        "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
    }


@router.post("/semantic-layer/init/stream")
async def init_schema_stream(
    request: SchemaInitRequest,
    guard: TargetGuard = Depends(require_target_body),
):
    """Initialize semantic layer and stream progress via SSE."""

    async def _generator():
        service = SchemaService()
        options = SchemaInitOptions(
            enum_threshold=request.enum_threshold,
            force=request.force,
            sample_enums=request.sample_enums,
        )
        async for event in service.init_events(
            guard.target_name, guard.target_config, options
        ):
            yield _schema_event_to_sse(event)

    return EventSourceResponse(_generator())


@router.get("/semantic-layer/export")
async def export_schema(
    guard: TargetGuard = Depends(require_target),
    format: str = Query("yaml", description="Export format: yaml or json"),
) -> SchemaExportResponse:
    """Export semantic layer as YAML or JSON."""
    service = SchemaService()
    result = service.export(guard.target_name, format)
    return _export_result_to_response(result)


@router.delete("/semantic-layer")
async def delete_schema(
    guard: TargetGuard = Depends(require_target),
) -> SchemaDeleteResponse:
    """Delete semantic layer for a target."""
    service = SchemaService()
    result = service.delete(guard.target_name)
    return _delete_result_to_response(result)


@router.post("/semantic-layer/table")
async def add_table(
    request: AddTableRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaUpdateResponse:
    """Add or update a table annotation."""
    service = SchemaService()
    result = service.add_table(
        guard.target_name,
        request.table_name,
        request.description,
        request.business_context,
        request.row_estimate,
    )
    return _update_result_to_response(result)


@router.post("/semantic-layer/column")
async def add_column(
    request: AddColumnRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaUpdateResponse:
    """Add or update a column annotation."""
    service = SchemaService()
    result = service.add_column(
        guard.target_name,
        request.table_name,
        request.column_name,
        request.description,
        request.data_type,
        request.unit,
        request.is_pii,
    )
    return _update_result_to_response(result)


@router.post("/semantic-layer/enum")
async def add_enum(
    request: AddEnumRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaUpdateResponse:
    """Add enum value mappings for a column."""
    service = SchemaService()
    result = service.add_enum(
        guard.target_name,
        request.table_name,
        request.column_name,
        request.enum_values,
    )
    return _update_result_to_response(result)


@router.post("/semantic-layer/terminology")
async def add_terminology(
    request: AddTerminologyRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaUpdateResponse:
    """Add a terminology entry."""
    service = SchemaService()
    result = service.add_terminology(
        guard.target_name,
        request.term,
        request.definition,
        request.sql_pattern,
        request.synonyms,
    )
    return _update_result_to_response(result)


@router.post("/semantic-layer/relationship")
async def add_relationship(
    request: AddRelationshipRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaUpdateResponse:
    """Add a relationship between tables."""
    service = SchemaService()
    result = service.add_relationship(
        guard.target_name,
        request.source_table,
        request.target_table,
        request.join_pattern,
        request.relationship_type,
    )
    return _update_result_to_response(result)


@router.post("/semantic-layer/metric")
async def add_metric(
    request: AddMetricRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SchemaUpdateResponse:
    """Add or update a metric definition."""
    service = SchemaService()
    result = service.add_metric(
        guard.target_name,
        request.name,
        request.definition,
        request.sql,
        request.unit,
    )
    return _update_result_to_response(result)


@router.post("/semantic-layer/annotate")
async def annotate_schema(
    request: AnnotateRequest,
    guard: TargetGuard = Depends(require_target_body),
):
    """Use LLM to generate descriptions for tables and columns (SSE streaming)."""
    return EventSourceResponse(
        _annotate_generator(
            guard.target_name,
            guard.target_config,
            request.table_name,
            request.sample_rows,
        )
    )


@router.post(
    "/semantic-layer/annotation-runs",
    response_model=AnnotationRunStartResponse,
)
async def start_annotation_run(
    request: AnnotateRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> AnnotationRunStartResponse:
    """Start AI annotation as a process-local background run."""
    existing = _registry.find_active("schema_annotation", guard.target_name)
    if existing is not None:
        return AnnotationRunStartResponse(run_id=existing, reused=True)

    bootstrap = _registry.find_active("bootstrap", guard.target_name)
    if bootstrap is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Database setup is already running for '{guard.target_name}'. "
                "Wait for it to finish before starting AI annotation."
            ),
        )

    service = AnnotateService()
    generator = service.annotate(
        guard.target_name,
        guard.target_config,
        request.table_name,
        request.sample_rows,
    )
    run_id = _registry.start("schema_annotation", guard.target_name, generator)
    return AnnotationRunStartResponse(run_id=run_id)


def _annotate_event_to_sse(event) -> dict:
    """Convert AnnotateEvent to SSE format."""
    if isinstance(event, AnnotateStartedEvent):
        return {
            "event": "started",
            "data": json.dumps(
                {
                    "tables": event.tables,
                    "completed_tables": event.completed_tables,
                    "message": event.message,
                }
            ),
        }
    if isinstance(event, AnnotateProgressEvent):
        return {
            "event": "progress",
            "data": json.dumps(
                {
                    "table": event.table,
                    "table_index": event.table_index,
                    "total_tables": event.total_tables,
                    "message": event.message,
                }
            ),
        }
    if isinstance(event, AnnotateTableCompleteEvent):
        return {
            "event": "table_complete",
            "data": json.dumps(
                {
                    "table": event.table,
                    "table_index": event.table_index,
                    "total_tables": event.total_tables,
                    "columns_annotated": event.columns_annotated,
                }
            ),
        }
    if isinstance(event, AnnotateCompleteEvent):
        return {
            "event": "complete",
            "data": json.dumps(
                {
                    "success": event.success,
                    "tables_annotated": event.tables_annotated,
                    "columns_annotated": event.columns_annotated,
                    "message": event.message,
                }
            ),
        }
    if isinstance(event, AnnotateErrorEvent):
        return {
            "event": "error",
            "data": json.dumps({"message": event.message}),
        }
    return {
        "event": "unknown",
        "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
    }


async def _annotate_generator(
    target: str,
    target_config: dict[str, Any],
    table_name: Optional[str],
    sample_rows: int,
):
    """Async generator that yields SSE events for annotation progress."""
    service = AnnotateService()

    async for event in service.annotate(
        target,
        target_config,
        table_name,
        sample_rows,
    ):
        yield _annotate_event_to_sse(event)
