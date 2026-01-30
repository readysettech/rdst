"""API routes for semantic layer management."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .target_guard import TargetGuard, require_target, require_target_body
from ...services.schema_service import SchemaService
from ...services.types import (
    SchemaStatus,
    SchemaDetails,
    SchemaTable,
    SchemaTableColumn,
    SchemaTableRelationship,
    SchemaTerminology,
    SchemaMetric,
    SchemaExtension,
    SchemaCustomType,
    SchemaTargetSummary,
    SchemaTargetList,
    SchemaInitOptions,
    SchemaInitResult,
    SchemaExportResult,
    SchemaDeleteResult,
    SchemaUpdateResult,
    SchemaCompleteEvent,
    SchemaErrorEvent,
    SchemaEvent,
    SchemaStatusEvent,
)

router = APIRouter(tags=["semantic-layer"])


# ============================================================================
# Pydantic Response Models
# ============================================================================


class SchemaStatusResponse(BaseModel):
    target: str
    exists: bool
    tables: int
    columns: int
    relationships: int
    terminology: int
    updated_at: Optional[str] = None


class SchemaTableColumnResponse(BaseModel):
    name: str
    data_type: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    is_pii: bool = False
    enum_values: Optional[Dict[str, str]] = None


class SchemaTableRelationshipResponse(BaseModel):
    target_table: str
    relationship_type: str
    join_pattern: str


class SchemaTableResponse(BaseModel):
    name: str
    description: Optional[str] = None
    business_context: Optional[str] = None
    row_estimate: Optional[str] = None
    columns: List[SchemaTableColumnResponse]
    relationships: List[SchemaTableRelationshipResponse]


class SchemaTerminologyResponse(BaseModel):
    term: str
    definition: str
    sql_pattern: str
    synonyms: List[str]


class SchemaMetricResponse(BaseModel):
    name: str
    definition: str
    sql: str


class SchemaExtensionResponse(BaseModel):
    name: str
    version: str
    description: Optional[str] = None
    types_provided: List[str]


class SchemaCustomTypeResponse(BaseModel):
    name: str
    type_category: str
    base_type: Optional[str] = None
    enum_values: Optional[List[str]] = None
    description: Optional[str] = None


class SchemaDetailsResponse(BaseModel):
    target: str
    tables: List[SchemaTableResponse]
    terminology: List[SchemaTerminologyResponse]
    extensions: List[SchemaExtensionResponse]
    custom_types: List[SchemaCustomTypeResponse]
    metrics: List[SchemaMetricResponse]


class SchemaTargetSummaryResponse(BaseModel):
    name: str
    tables: int
    terminology: int
    updated_at: Optional[str] = None


class SchemaTargetListResponse(BaseModel):
    targets: List[SchemaTargetSummaryResponse]


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
    enum_columns: List[str]
    path: Optional[str] = None
    error: Optional[str] = None


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
    enum_values: Dict[str, str]


class AddTerminologyRequest(BaseModel):
    target: str
    term: str
    definition: str
    sql_pattern: str
    synonyms: Optional[List[str]] = None


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


# ============================================================================
# Converters (dataclass -> Pydantic)
# ============================================================================


def _status_to_response(status: SchemaStatus) -> SchemaStatusResponse:
    return SchemaStatusResponse(
        target=status.target,
        exists=status.exists,
        tables=status.tables,
        columns=status.columns,
        relationships=status.relationships,
        terminology=status.terminology,
        updated_at=status.updated_at,
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


# ============================================================================
# API Endpoints
# ============================================================================


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
        raise HTTPException(
            status_code=404,
            detail=f"No semantic layer found for target '{guard.target_name}'"
            + (f" or table '{table}' not found" if table else ""),
        )

    return _details_to_response(details)


@router.get("/semantic-layer/targets")
async def list_schema_targets() -> SchemaTargetListResponse:
    """List all targets with semantic layers."""
    service = SchemaService()
    target_list = service.list_targets()
    return _target_list_to_response(target_list)


@router.post("/semantic-layer/init")
async def init_schema(request: SchemaInitRequest, guard: TargetGuard = Depends(require_target_body)) -> SchemaInitResponse:
    """Initialize semantic layer by introspecting database schema."""
    service = SchemaService()
    options = SchemaInitOptions(
        enum_threshold=request.enum_threshold,
        force=request.force,
        sample_enums=request.sample_enums,
    )

    init_result = None
    error_message = None

    async for event in service.init_events(guard.target_name, guard.target_config, options):
        if isinstance(event, SchemaCompleteEvent) and event.init_result:
            init_result = event.init_result
        elif isinstance(event, SchemaErrorEvent):
            error_message = event.message

    if init_result is not None:
        return _init_result_to_response(init_result)

    return SchemaInitResponse(
        success=False,
        target=guard.target_name,
        tables=0,
        columns=0,
        relationships=0,
        enum_columns=[],
        error=error_message or "Schema initialization failed",
    )


def _schema_event_to_sse(event: SchemaEvent) -> dict:
    """Convert schema service events to SSE payloads."""
    import json

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
        "data": json.dumps(
            {
                "message": f"Unknown event type: {type(event)}",
            }
        ),
    }


@router.post("/semantic-layer/init/stream")
async def init_schema_stream(request: SchemaInitRequest, guard: TargetGuard = Depends(require_target_body)):
    """Initialize semantic layer and stream progress via SSE."""

    async def _generator():
        service = SchemaService()
        options = SchemaInitOptions(
            enum_threshold=request.enum_threshold,
            force=request.force,
            sample_enums=request.sample_enums,
        )
        async for event in service.init_events(guard.target_name, guard.target_config, options):
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
async def add_table(request: AddTableRequest, guard: TargetGuard = Depends(require_target_body)) -> SchemaUpdateResponse:
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
async def add_column(request: AddColumnRequest, guard: TargetGuard = Depends(require_target_body)) -> SchemaUpdateResponse:
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
async def add_enum(request: AddEnumRequest, guard: TargetGuard = Depends(require_target_body)) -> SchemaUpdateResponse:
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
async def add_terminology(request: AddTerminologyRequest, guard: TargetGuard = Depends(require_target_body)) -> SchemaUpdateResponse:
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
async def add_relationship(request: AddRelationshipRequest, guard: TargetGuard = Depends(require_target_body)) -> SchemaUpdateResponse:
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
async def add_metric(request: AddMetricRequest, guard: TargetGuard = Depends(require_target_body)) -> SchemaUpdateResponse:
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
async def annotate_schema(request: AnnotateRequest, guard: TargetGuard = Depends(require_target_body)):
    """Use LLM to generate descriptions for tables and columns (SSE streaming)."""
    return EventSourceResponse(
        _annotate_generator(
            guard.target_name,
            guard.target_config,
            request.table_name,
            request.sample_rows,
        )
    )


def _annotate_event_to_sse(event) -> dict:
    """Convert AnnotateEvent to SSE format."""
    import json
    from ...services.types import (
        AnnotateStartedEvent,
        AnnotateProgressEvent,
        AnnotateTableCompleteEvent,
        AnnotateCompleteEvent,
        AnnotateErrorEvent,
    )

    if isinstance(event, AnnotateStartedEvent):
        return {
            "event": "started",
            "data": json.dumps(
                {
                    "tables": event.tables,
                    "message": event.message,
                }
            ),
        }
    elif isinstance(event, AnnotateProgressEvent):
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
    elif isinstance(event, AnnotateTableCompleteEvent):
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
    elif isinstance(event, AnnotateCompleteEvent):
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
    elif isinstance(event, AnnotateErrorEvent):
        return {
            "event": "error",
            "data": json.dumps(
                {
                    "message": event.message,
                }
            ),
        }
    else:
        return {
            "event": "unknown",
            "data": json.dumps({"message": f"Unknown event type: {type(event)}"}),
        }


async def _annotate_generator(
    target: str,
    target_config: Dict[str, Any],
    table_name: Optional[str],
    sample_rows: int,
):
    """Async generator that yields SSE events for annotation progress."""
    from ...services.annotate_service import AnnotateService

    # Use the shared AnnotateService
    service = AnnotateService()

    async for event in service.annotate(
        target,
        target_config,
        table_name,
        sample_rows,
    ):
        yield _annotate_event_to_sse(event)
