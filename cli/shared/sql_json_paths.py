"""Recognize JSON path literals so query normalizers keep them intact.

In `place.name->>'$.address.city' = 'xxx'` only `'xxx'` is data. The path
`'$.address.city'` names a key inside the document; it is part of the query's
structure, the same way a column name is. Turning it into a placeholder
merges queries that read different keys into one shape and produces text that
cannot be executed. This module classifies quoted literals so the regex-based
normalizers can leave paths alone.

Recognized forms:
- right operand of the JSON operators `->`, `->>`, `#>`, `#>>` (MySQL, PostgreSQL)
- literals starting with `$` (MySQL JSON paths and SQL/JSON path expressions)
- non-first arguments of JSON path functions such as JSON_EXTRACT,
  JSON_VALUE, JSON_CONTAINS_PATH, jsonb_path_exists, jsonb_extract_path
"""

import re
from typing import Callable, Iterator, List, Tuple

STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

_JSON_OPERATOR_RE = re.compile(r"(->>|->|#>>|#>)\s*$")

_JSON_PATH_FUNCTIONS = {
    "json_extract",
    "json_value",
    "json_query",
    "json_contains_path",
    "json_search",
    "json_keys",
    "json_length",
    "json_table",
    "json_exists",
    "jsonb_path_exists",
    "jsonb_path_match",
    "jsonb_path_query",
    "jsonb_path_query_array",
    "jsonb_path_query_first",
    "jsonb_path_exists_tz",
    "jsonb_path_query_tz",
    "jsonb_extract_path",
    "jsonb_extract_path_text",
    "json_extract_path",
    "json_extract_path_text",
}

_FUNCTION_CALL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\($")


def find_string_literals(sql: str) -> Iterator[re.Match]:
    return STRING_LITERAL_RE.finditer(sql)


def is_json_path_literal(sql: str, start: int, end: int) -> bool:
    """Whether the quoted literal at sql[start:end] is a JSON path rather than data."""
    body = sql[start + 1 : end - 1]
    if body.startswith("$"):
        return True
    before = sql[:start]
    if _JSON_OPERATOR_RE.search(before):
        return True
    return _is_later_json_function_argument(before)


def _is_later_json_function_argument(before: str) -> bool:
    """True when `before` ends inside a JSON path function call, past its first argument."""
    depth = 0
    saw_comma = False
    for idx in range(len(before) - 1, -1, -1):
        ch = before[idx]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                match = _FUNCTION_CALL_RE.search(before[: idx + 1])
                return bool(match) and saw_comma and match.group(1).lower() in _JSON_PATH_FUNCTIONS
            depth -= 1
        elif ch == "," and depth == 0:
            saw_comma = True
        elif ch == ";":
            return False
    return False


def mask_json_paths(sql: str) -> Tuple[str, Callable[[str], str]]:
    """Replace JSON path literals with opaque sentinels; returns (masked_sql, restore).

    Sentinels contain no word characters, digits or whitespace, so the
    normalizers' literal, numeric, date and keyword passes leave them alone.
    """
    kept: List[str] = []
    out: List[str] = []
    cursor = 0
    for match in find_string_literals(sql):
        if is_json_path_literal(sql, match.start(), match.end()):
            out.append(sql[cursor : match.start()])
            out.append("\x00" + "\x01" * (len(kept) + 1) + "\x00")
            kept.append(match.group(0))
            cursor = match.end()
    out.append(sql[cursor:])

    def restore(text: str) -> str:
        return re.sub(r"\x00(\x01+)\x00", lambda m: kept[len(m.group(1)) - 1], text)

    return "".join(out), restore
