"""Schema API routes."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.api.target_guard import TargetGuard, require_target_optional

from ..schema_collector import collect_all_tables_schema

router = APIRouter()


class SchemaResponse(BaseModel):
    tables: dict[str, list[str]]
    dialect: Literal["postgresql", "mysql"]
    error: Optional[str] = None
    code: Optional[str] = None
    category: Optional[str] = None
    target: Optional[str] = None


@router.get("/schema")
async def get_schema(
    guard: TargetGuard = Depends(require_target_optional),
) -> SchemaResponse:
    """Fetch database schema for SQL autocomplete."""
    try:
        result = collect_all_tables_schema(
            target_config=guard.target_config,
            target=guard.target_name,
        )
        if not result.get("success"):
            from shared.api.ssh_errors import connectivity_error_payload

            raw_error = result.get("error", "Failed to collect schema")
            failure = connectivity_error_payload(
                RuntimeError(raw_error), guard.target_name, guard.target_config
            )
            return SchemaResponse(
                tables={},
                dialect="postgresql",
                error=failure["message"] if failure else raw_error,
                code=failure["category"] if failure else None,
                category=failure["category"] if failure else None,
                target=guard.target_name,
            )

        engine = guard.target_config.get("engine", "postgresql")
        dialect: Literal["postgresql", "mysql"] = (
            "mysql" if engine == "mysql" else "postgresql"
        )
        return SchemaResponse(
            tables=result.get("tables", {}),
            dialect=dialect,
        )
    except HTTPException:
        raise
    except Exception as exc:
        from shared.api.ssh_errors import connectivity_error_payload

        failure = connectivity_error_payload(
            exc, guard.target_name, guard.target_config
        )
        return SchemaResponse(
            tables={},
            dialect="postgresql",
            error=failure["message"] if failure else str(exc),
            code=failure["category"] if failure else None,
            category=failure["category"] if failure else None,
            target=guard.target_name,
        )


def _parse_schema_to_tables(schema_info: str) -> dict[str, list[str]]:
    """Parse schema info string into table -> columns mapping."""
    tables: dict[str, list[str]] = {}
    if not schema_info:
        return tables

    current_table = None
    for line in schema_info.split("\n"):
        line = line.strip()
        if line.startswith("Table:"):
            current_table = line.replace("Table:", "").strip()
            if current_table and current_table not in tables:
                tables[current_table] = []
        elif line.startswith("- ") and current_table:
            col_info = line[2:].strip()
            col_name = col_info.split(":")[0].split("(")[0].strip()
            if col_name and col_name not in tables[current_table]:
                tables[current_table].append(col_name)
        elif line.startswith("CREATE TABLE"):
            match = line.split("CREATE TABLE")[-1].strip()
            table_name = match.split("(")[0].strip().strip('"').strip("'")
            if "." in table_name:
                table_name = table_name.split(".")[-1]
            if table_name:
                current_table = table_name
                tables[current_table] = []
        elif (
            current_table
            and "(" not in line
            and ")" not in line
            and line
            and not line.startswith("--")
        ):
            parts = line.split()
            if parts:
                col_name = parts[0].strip(",").strip('"').strip("'")
                if col_name and col_name.upper() not in (
                    "PRIMARY",
                    "FOREIGN",
                    "UNIQUE",
                    "CHECK",
                    "CONSTRAINT",
                    "INDEX",
                ):
                    if col_name not in tables.get(current_table, []):
                        tables.setdefault(current_table, []).append(col_name)

    return tables
