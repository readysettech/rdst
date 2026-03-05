"""
Unit tests for result count pluralization (rdst-2vr.17).

'1 rows' should be '1 row' — singular when count is 1.
"""

import re
from pathlib import Path


_FILES_WITH_ROW_TITLES = [
    "lib/engines/ask3/renderer.py",
    "lib/engines/ask3/presenter.py",
    "lib/functions/result_display.py",
]


class TestResultRowPluralization:
    """Result headers must use singular 'row' when count is 1."""

    def test_no_hardcoded_rows_in_title_fstrings(self):
        """Title f-strings must not hardcode 'rows' — must pluralize correctly."""
        root = Path(__file__).parent.parent.parent

        violations = []
        # Pattern: f-string with a variable followed by literal ' rows'
        # e.g., f"Results ({row_count} rows," or f"({total_rows} rows,"
        pattern = re.compile(r'f"[^"]*\{[^}]+\}\s+rows[,)]')

        for rel_path in _FILES_WITH_ROW_TITLES:
            source = (root / rel_path).read_text()
            for i, line in enumerate(source.splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"  {rel_path}:{i}: {line.strip()}")

        assert not violations, (
            "Hardcoded 'rows' in title f-strings (should pluralize: "
            "'row' when count==1, 'rows' otherwise):\n" + "\n".join(violations)
        )
