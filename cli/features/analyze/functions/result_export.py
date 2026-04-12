"""
Result Export Functions

Export query results to various formats (CSV, JSON, TSV).
"""

import csv
import json
import io
from typing import List, Any, Dict, Optional
from datetime import datetime, date


def export_to_csv(
    columns: List[str],
    rows: List[List[Any]],
    include_header: bool = True
) -> str:
    """
    Export results to CSV format.

    Args:
        columns: Column names
        rows: Result rows
        include_header: Whether to include header row

    Returns:
        CSV-formatted string
    """
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Write header
    if include_header:
        writer.writerow(columns)

    # Write rows
    for row in rows:
        # Convert values to strings, handle special types
        csv_row = [_format_value_for_csv(val) for val in row]
        writer.writerow(csv_row)

    return output.getvalue()


def export_to_json(
    columns: List[str],
    rows: List[List[Any]],
    metadata: Optional[Dict[str, Any]] = None,
    pretty: bool = True
) -> str:
    """
    Export results to JSON format.

    Args:
        columns: Column names
        rows: Result rows
        metadata: Optional metadata (query info, timing, etc.)
        pretty: Whether to pretty-print JSON

    Returns:
        JSON-formatted string
    """
    # Convert rows to list of dicts
    result_rows = []
    for row in rows:
        row_dict = {}
        for col, val in zip(columns, row):
            row_dict[col] = _format_value_for_json(val)
        result_rows.append(row_dict)

    # Build output structure
    output = {
        'rows': result_rows
    }

    # Add metadata if provided
    if metadata:
        output['metadata'] = {
            'row_count': len(rows),
            'columns': columns,
            **metadata
        }

    # Serialize to JSON
    if pretty:
        return json.dumps(output, indent=2, default=str)
    else:
        return json.dumps(output, default=str)


def export_to_tsv(
    columns: List[str],
    rows: List[List[Any]],
    include_header: bool = True
) -> str:
    """
    Export results to TSV (Tab-Separated Values) format.

    Args:
        columns: Column names
        rows: Result rows
        include_header: Whether to include header row

    Returns:
        TSV-formatted string
    """
    output = io.StringIO()
    writer = csv.writer(output, delimiter='\t', quoting=csv.QUOTE_MINIMAL)

    # Write header
    if include_header:
        writer.writerow(columns)

    # Write rows
    for row in rows:
        tsv_row = [_format_value_for_csv(val) for val in row]
        writer.writerow(tsv_row)

    return output.getvalue()


def _format_value_for_csv(value: Any) -> str:
    """Format a value for CSV export."""
    if value is None:
        return ''

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, bool):
        return 'true' if value else 'false'

    if isinstance(value, (int, float)):
        return str(value)

    # Convert to string
    return str(value)


def _format_value_for_json(value: Any) -> Any:
    """Format a value for JSON export."""
    if value is None:
        return None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, (int, float, bool, str)):
        return value

    # Convert other types to string
    return str(value)


def export_results(
    format: str,
    columns: List[str],
    rows: List[List[Any]],
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Export results in specified format.

    Args:
        format: Export format ('csv', 'json', 'tsv')
        columns: Column names
        rows: Result rows
        metadata: Optional metadata

    Returns:
        Formatted export string

    Raises:
        ValueError: If format is not supported
    """
    format_lower = format.lower()

    if format_lower == 'csv':
        return export_to_csv(columns, rows)

    elif format_lower == 'json':
        return export_to_json(columns, rows, metadata)

    elif format_lower == 'tsv':
        return export_to_tsv(columns, rows)

    else:
        raise ValueError(f"Unsupported export format: {format}")
