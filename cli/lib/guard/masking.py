"""
Output masking - Apply masking patterns to query results.

Supported mask types:
    redact      -> [REDACTED]
    email       -> u***@d***.com
    partial:N   -> ****1234 (show last N chars)
    hash        -> a1b2c3d4 (SHA256 truncated)
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from typing import Any


def mask_value(value: Any, mask_type: str) -> Any:
    """Apply a mask pattern to a single value.

    Args:
        value: The value to mask.
        mask_type: The type of mask to apply.

    Returns:
        The masked value.
    """
    if value is None:
        return None

    # Convert to string for masking
    str_value = str(value)

    if mask_type == "redact":
        return "[REDACTED]"

    elif mask_type == "email":
        return _mask_email(str_value)

    elif mask_type.startswith("partial:"):
        try:
            n = int(mask_type.split(":")[1])
            return _mask_partial(str_value, n)
        except (ValueError, IndexError):
            return "[REDACTED]"

    elif mask_type == "hash":
        return _mask_hash(str_value)

    else:
        # Unknown mask type, default to redact
        return "[REDACTED]"


def _mask_email(value: str) -> str:
    """Mask an email address, preserving structure.

    Example: user@company.com -> u***@c*****.com
    """
    if "@" not in value:
        return "[REDACTED]"

    parts = value.split("@")
    if len(parts) != 2:
        return "[REDACTED]"

    local, domain = parts

    # Mask local part (keep first char)
    if len(local) > 1:
        masked_local = local[0] + "*" * min(3, len(local) - 1)
    else:
        masked_local = "*"

    # Mask domain (keep first char and TLD)
    domain_parts = domain.rsplit(".", 1)
    if len(domain_parts) == 2:
        domain_name, tld = domain_parts
        if len(domain_name) > 1:
            masked_domain = domain_name[0] + "*" * min(5, len(domain_name) - 1)
        else:
            masked_domain = "*"
        return f"{masked_local}@{masked_domain}.{tld}"
    else:
        return f"{masked_local}@***.***"


def _mask_partial(value: str, keep_last: int) -> str:
    """Mask a value, showing only the last N characters.

    Example: 4111111111111111 with keep_last=4 -> ************1111
    """
    if len(value) <= keep_last:
        return "*" * len(value)

    masked_len = len(value) - keep_last
    return "*" * masked_len + value[-keep_last:]


def _mask_hash(value: str) -> str:
    """Hash a value using SHA256, returning first 8 hex chars.

    Provides consistent pseudonymization - same input always produces same output.
    """
    hash_bytes = hashlib.sha256(value.encode()).hexdigest()
    return hash_bytes[:8]


def mask_results(
    columns: list[str],
    rows: list[list[Any]],
    masked_columns: dict[str, str] | None,
) -> list[list[Any]]:
    """Apply masking patterns to query results.

    Args:
        columns: List of column names.
        rows: List of row data (each row is a list of values).
        masked_columns: Dict mapping column patterns to mask types.
            Example: {"*.email": "email", "users.ssn": "redact"}

    Returns:
        New list of rows with masked values.
    """
    if not masked_columns or not rows:
        return rows

    # Build column index -> mask type mapping
    column_masks: dict[int, str] = {}
    for idx, col_name in enumerate(columns):
        for pattern, mask_type in masked_columns.items():
            # Support both "column" and "table.column" patterns
            if _column_matches_pattern(col_name, pattern):
                column_masks[idx] = mask_type
                break

    if not column_masks:
        return rows

    # Apply masks to each row
    masked_rows = []
    for row in rows:
        masked_row = list(row)  # Copy
        for idx, mask_type in column_masks.items():
            if idx < len(masked_row):
                masked_row[idx] = mask_value(masked_row[idx], mask_type)
        masked_rows.append(masked_row)

    return masked_rows


def _column_matches_pattern(column_name: str, pattern: str) -> bool:
    """Check if a column name matches a pattern.

    Supports:
    - Exact match: "email" matches "email"
    - Wildcard: "*.email" matches "users.email" or "email"
    - Table.column: "users.email" matches "users.email"
    """
    # Normalize column name (lowercase)
    col_lower = column_name.lower()
    pattern_lower = pattern.lower()

    # Direct match
    if col_lower == pattern_lower:
        return True

    # Pattern with wildcard
    if fnmatch.fnmatch(col_lower, pattern_lower):
        return True

    # Pattern like "*.email" should match bare "email"
    if pattern_lower.startswith("*.") and col_lower == pattern_lower[2:]:
        return True

    return False


def get_masked_columns(
    columns: list[str],
    masked_columns: dict[str, str] | None,
) -> list[str]:
    """Get list of column names that will be masked.

    Useful for showing warnings about which columns will be affected.

    Args:
        columns: List of column names in query result.
        masked_columns: Dict mapping column patterns to mask types.

    Returns:
        List of column names that match masking patterns.
    """
    if not masked_columns:
        return []

    masked = []
    for col_name in columns:
        for pattern in masked_columns.keys():
            if _column_matches_pattern(col_name, pattern):
                masked.append(col_name)
                break

    return masked
