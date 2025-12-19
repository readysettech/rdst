"""
Schema Collection from YAML Semantic Layer

Loads schema information from the semantic layer YAML file instead of
querying the live database. Used for shallow analysis mode where DB
connection is not available at scan time.
"""

from pathlib import Path
from typing import Dict, Any

from ..data_structures.semantic_layer import SemanticLayer


def collect_schema_from_yaml(target: str = None, **kwargs) -> Dict[str, Any]:
    """
    Load schema information from semantic layer YAML file.

    This is the shallow-mode alternative to collect_target_schema.
    Instead of querying the live database, it loads cached schema
    information from ~/.rdst/semantic-layer/{target}.yaml

    Args:
        target: Target database name
        **kwargs: Additional workflow parameters (ignored)

    Returns:
        Dict containing:
        - success: boolean indicating if load succeeded
        - schema_info: formatted schema string for LLM prompt
        - tables_analyzed: list of table names
        - source: "yaml" to indicate source
        - error: error message if failed
    """
    if not target:
        return {
            "success": False,
            "schema_info": "Schema information: Not available",
            "tables_analyzed": [],
            "source": "yaml",
            "error": "No target specified"
        }

    # Check if semantic layer YAML exists
    schema_path = Path.home() / ".rdst" / "semantic-layer" / f"{target}.yaml"

    if not schema_path.exists():
        return {
            "success": False,
            "schema_info": "Schema information: Not available",
            "tables_analyzed": [],
            "source": "yaml",
            "error": f"No schema found for target '{target}'. Run 'rdst schema init --target {target}' first."
        }

    try:
        # Load semantic layer
        layer = SemanticLayer.load(schema_path)

        # Format schema info for LLM prompt
        schema_info = _format_schema_for_llm(layer)
        table_names = list(layer.tables.keys())

        return {
            "success": True,
            "schema_info": schema_info,
            "tables_analyzed": table_names,
            "source": "yaml",
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "schema_info": "Schema information: Failed to load",
            "tables_analyzed": [],
            "source": "yaml",
            "error": f"Failed to load schema YAML: {str(e)}"
        }


def _format_schema_for_llm(layer: SemanticLayer) -> str:
    """
    Format semantic layer as schema string for LLM prompt.

    Includes:
    - Table descriptions and row estimates
    - Column names, types, and descriptions
    - Indexes (critical for shallow analysis)
    - Enum values
    - Extension info
    """
    parts = ["Schema information:"]

    for table_name, table in layer.tables.items():
        # Table header
        table_parts = [f"\nTable: {table_name}"]

        # Row estimate
        if table.row_estimate:
            table_parts.append(f"Row estimate: {table.row_estimate}")

        # Description
        if table.description:
            table_parts.append(f"Description: {table.description}")

        # Columns
        table_parts.append("Columns:")
        for col_name, col in table.columns.items():
            col_str = f"  - {col_name}"
            if col.data_type:
                col_str += f" {col.data_type}"
            if col.description:
                col_str += f" -- {col.description}"
            if col.enum_values:
                enum_preview = ", ".join(list(col.enum_values.keys())[:5])
                if len(col.enum_values) > 5:
                    enum_preview += f"... ({len(col.enum_values)} total)"
                col_str += f" [enum: {enum_preview}]"
            table_parts.append(col_str)

        # Indexes (critical for shallow analysis)
        if hasattr(table, 'indexes') and table.indexes:
            table_parts.append("Indexes:")
            for idx_name, idx in table.indexes.items():
                cols_str = ", ".join(idx.columns)
                idx_str = f"  - {idx_name}"
                if idx.is_primary:
                    idx_str += " PRIMARY KEY"
                elif idx.is_unique:
                    idx_str += " UNIQUE"
                idx_str += f" USING {idx.index_type} ({cols_str})"
                if idx.definition:
                    # Include full definition for PostgreSQL
                    idx_str = f"  - {idx.definition}"
                table_parts.append(idx_str)

        # Relationships
        if table.relationships:
            table_parts.append("Relationships:")
            for rel in table.relationships:
                table_parts.append(f"  - {rel.relationship_type} to {rel.target_table}: {rel.join_pattern}")

        parts.append('\n'.join(table_parts))

    # Add extensions and custom types context if available
    extensions_context = layer.get_extensions_context()
    if extensions_context:
        parts.append(f"\n{extensions_context}")

    return '\n'.join(parts)
