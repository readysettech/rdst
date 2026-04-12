"""Schema feature event models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from .models import (
    SchemaDeleteResult,
    SchemaDetails,
    SchemaExportResult,
    SchemaInitResult,
    SchemaStatus,
    SchemaTargetList,
    SchemaUpdateResult,
)


@dataclass
class SchemaStatusEvent:
    """Status update for schema operations."""

    type: Literal["status"]
    operation: str
    message: str


@dataclass
class SchemaCompleteEvent:
    """Schema operation completion event."""

    type: Literal["complete"]
    operation: str
    success: bool
    status: SchemaStatus | None = None
    details: SchemaDetails | None = None
    target_list: SchemaTargetList | None = None
    init_result: SchemaInitResult | None = None
    export_result: SchemaExportResult | None = None
    delete_result: SchemaDeleteResult | None = None
    update_result: SchemaUpdateResult | None = None


@dataclass
class SchemaErrorEvent:
    """Schema operation error event."""

    type: Literal["error"]
    operation: str
    message: str


SchemaEvent = Union[SchemaStatusEvent, SchemaCompleteEvent, SchemaErrorEvent]


@dataclass
class AnnotateStartedEvent:
    """Annotation process started."""

    type: Literal["annotate_started"]
    tables: int
    message: str


@dataclass
class AnnotateProgressEvent:
    """Progress update during annotation."""

    type: Literal["annotate_progress"]
    table: str
    table_index: int
    total_tables: int
    message: str


@dataclass
class AnnotateTableCompleteEvent:
    """A table has been annotated."""

    type: Literal["annotate_table_complete"]
    table: str
    table_index: int
    total_tables: int
    columns_annotated: int


@dataclass
class AnnotateCompleteEvent:
    """Annotation process completed successfully."""

    type: Literal["annotate_complete"]
    success: bool
    tables_annotated: int
    columns_annotated: int
    message: str


@dataclass
class AnnotateErrorEvent:
    """Annotation process encountered an error."""

    type: Literal["annotate_error"]
    message: str


AnnotateEvent = Union[
    AnnotateStartedEvent,
    AnnotateProgressEvent,
    AnnotateTableCompleteEvent,
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
]
