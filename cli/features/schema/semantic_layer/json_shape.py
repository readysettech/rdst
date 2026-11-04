"""
JSON column shape detection

Summarizes the key paths found in sampled JSON documents, so a semantic layer can
describe what a json/jsonb column holds: each path with its value type and one
example value.
"""

import json
from collections import Counter

JSON_SAMPLE_ROWS = 100
MAX_JSON_KEYS = 60
MAX_JSON_DEPTH = 5
MAX_EXAMPLE_CHARS = 40

_TYPE_NAMES = {bool: "boolean", int: "number", float: "number", str: "string"}


# Tables above this many rows are read through a sample of about SCAN_ROWS rows, so a
# mostly-null column cannot turn the probe into a full scan.
LARGE_TABLE_ROWS = 50_000
SCAN_ROWS = 10_000


def json_sample_sql_postgres(table: str, column: str, row_estimate: int, marker: str) -> str:
    """Bounded sample of non-null documents; identifiers must already be quoted."""
    sample = ""
    if row_estimate > LARGE_TABLE_ROWS:
        sample = f" TABLESAMPLE SYSTEM({max(0.5, SCAN_ROWS / row_estimate * 100)})"
    return (
        f"{marker}SELECT {column}::text FROM {table}{sample} "
        f"WHERE {column} IS NOT NULL LIMIT {JSON_SAMPLE_ROWS}"
    )


def json_sample_sql_mysql(table: str, column: str, row_estimate: int, marker: str) -> str:
    """Bounded sample of non-null documents; identifiers must already be quoted."""
    source = table
    if row_estimate > LARGE_TABLE_ROWS:
        source = f"(SELECT {column} FROM {table} LIMIT {SCAN_ROWS}) AS sampled"
    return (
        f"{marker}SELECT CAST({column} AS CHAR) FROM {source} "
        f"WHERE {column} IS NOT NULL LIMIT {JSON_SAMPLE_ROWS}"
    )


def summarize_json_documents(documents: list) -> dict[str, str]:
    """
    Map each key path in the documents to its value type and an example.

    Paths join object keys with '.', and '[]' marks the elements of an array, e.g.
    'readings[].value'. Paths seen in the most documents are kept first, up to
    MAX_JSON_KEYS. Documents that are not valid JSON are skipped.
    """
    seen: Counter = Counter()
    types: dict[str, set[str]] = {}
    examples: dict[str, str] = {}
    for document in documents:
        if isinstance(document, (str, bytes)):
            try:
                document = json.loads(document)
            except ValueError:
                continue
        paths: set[str] = set()
        _walk(document, "", 0, paths, types, examples)
        seen.update(paths)

    # Objects and arrays whose contents are listed add nothing on their own.
    parents = {path.rsplit(".", 1)[0] for path in seen if "." in path}
    parents |= {path[: path.rindex("[]")] for path in seen if "[]" in path}
    summary = {}
    for path, _ in sorted(seen.items(), key=lambda item: (-item[1], item[0])):
        if len(summary) >= MAX_JSON_KEYS:
            break
        if path in parents and types[path] <= {"object", "array"}:
            continue
        value_type = "|".join(sorted(types[path]))
        example = examples.get(path)
        summary[path] = f"{value_type}, e.g. {example}" if example else value_type
    return summary


def _walk(value, path, depth, paths, types, examples) -> None:
    if path:
        paths.add(path)
        types.setdefault(path, set()).add(_type_name(value))
        if path not in examples and value is not None and _type_name(value) in (
            "boolean",
            "number",
            "string",
        ):
            examples[path] = _example(value)
    if depth >= MAX_JSON_DEPTH:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _walk(item, f"{path}.{key}" if path else str(key), depth + 1, paths, types, examples)
    elif isinstance(value, list):
        for item in value:
            _walk(item, f"{path}[]", depth + 1, paths, types, examples)


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return _TYPE_NAMES.get(type(value), "string")


def _example(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > MAX_EXAMPLE_CHARS:
        text = text[: MAX_EXAMPLE_CHARS - 3] + "..."
    return text
