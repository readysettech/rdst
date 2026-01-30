from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from .target_guard import TargetGuard, require_target_optional

router = APIRouter()


class SchemaResponse(BaseModel):
    tables: dict[str, list[str]]
    dialect: str
    error: Optional[str] = None


@router.get("/schema")
async def get_schema(guard: TargetGuard = Depends(require_target_optional)) -> SchemaResponse:
    """Fetch database schema (tables and columns) for SQL autocomplete.

    Unlike the query analysis endpoint which collects schema only for tables
    referenced in a query, this endpoint collects ALL tables in the database
    for comprehensive autocomplete support.
    """
    try:
        from ...functions.schema_collector import collect_all_tables_schema

        result = collect_all_tables_schema(target_config=guard.target_config)

        if not result.get("success"):
            return SchemaResponse(
                tables={},
                dialect="postgresql",
                error=result.get("error", "Failed to collect schema"),
            )

        tables = result.get("tables", {})
        dialect = guard.target_config.get("engine", "postgresql")

        return SchemaResponse(
            tables=tables,
            dialect=dialect,
        )

    except HTTPException:
        raise
    except Exception as e:
        return SchemaResponse(
            tables={},
            dialect="postgresql",
            error=str(e),
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
