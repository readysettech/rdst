"""E2E tests for automatic schema enrichment.

Tests the full pipeline:
1. `rdst schema init` detects comma-separated list columns via value_pattern
2. `rdst schema show` renders pattern labels visibly
3. `rdst ask` generates correct SQL (splits comma-separated columns)

Run with:
    cd src && .venv/bin/python3 -m pytest tests/e2e/test_ask_enrichment.py -v
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_LLM_TIMEOUT = 90
_RDST_PY = Path(__file__).resolve().parent.parent.parent / "rdst.py"
_SEM_DIR = Path.home() / ".rdst" / "semantic-layer"


def _run_rdst(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_RDST_PY)] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(_RDST_PY.parent),
    )


def _extract_sql(output: str) -> str:
    """Extract SQL from rdst ask output rendered through tmux.

    The output format shows ``SQL: <statement>`` after the results table.
    Returns uppercased SQL string, or empty string if extraction fails.
    """
    match = re.search(
        r"\nSQL:\s*(.*?)(?:\nRows:|\nExecution time:|\Z)",
        output,
        re.DOTALL,
    )
    if not match:
        return ""
    block = match.group(1)
    block = re.sub(r"[│┌┐└┘─┬┴├┤╮╭╯╰]", " ", block)
    block = re.sub(r"\s+", " ", block).strip()
    return block.upper()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def e2e_semantic_layer(e2e_target):
    """Run ``rdst schema init`` and return the YAML path."""
    yaml_path = _SEM_DIR / f"{e2e_target}.yaml"
    if yaml_path.exists():
        yaml_path.unlink()

    result = _run_rdst("schema", "init", "--target", e2e_target, timeout=120)
    if result.returncode != 0:
        pytest.fail(
            f"'rdst schema init' failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[-500:]}\n"
            f"stderr: {result.stderr[-500:]}"
        )

    if not yaml_path.exists():
        pytest.fail(f"schema init succeeded but YAML not found at {yaml_path}")

    return yaml_path


# ---------------------------------------------------------------------------
# Test: schema init detects comma-separated columns
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestSchemaInit:
    """Validate that schema init detects delimiter-separated list columns."""

    def test_init_creates_semantic_layer(self, e2e_semantic_layer):
        """schema init should succeed and produce a YAML file."""
        assert e2e_semantic_layer.exists()

    def test_yaml_has_value_patterns(self, e2e_semantic_layer):
        """At least one column should have a detected value_pattern."""
        data = yaml.safe_load(e2e_semantic_layer.read_text())
        tables = data.get("tables", {})

        cols_with_patterns = []
        for tname, tdata in tables.items():
            for cname, cdata in tdata.get("columns", {}).items():
                if cdata.get("value_pattern"):
                    cols_with_patterns.append(
                        f"{tname}.{cname}: {cdata['value_pattern']}"
                    )

        assert cols_with_patterns, (
            "No columns have value_pattern after schema init. "
            "Pattern detection is not working."
        )

    def test_directors_detected_as_comma_separated(self, e2e_semantic_layer):
        """title_crew.directors must be detected as comma_separated_list."""
        data = yaml.safe_load(e2e_semantic_layer.read_text())
        directors = (
            data.get("tables", {})
            .get("title_crew", {})
            .get("columns", {})
            .get("directors", {})
        )

        assert directors, "title_crew.directors column not found in YAML"
        assert directors.get("value_pattern") == "comma_separated_list", (
            f"Expected title_crew.directors pattern='comma_separated_list', "
            f"got '{directors.get('value_pattern')}'"
        )

    def test_genres_detected_as_comma_separated(self, e2e_semantic_layer):
        """title_basics.genres must be detected as comma_separated_list."""
        data = yaml.safe_load(e2e_semantic_layer.read_text())
        genres = (
            data.get("tables", {})
            .get("title_basics", {})
            .get("columns", {})
            .get("genres", {})
        )

        assert genres, "title_basics.genres column not found in YAML"
        assert genres.get("value_pattern") == "comma_separated_list", (
            f"Expected title_basics.genres pattern='comma_separated_list', "
            f"got '{genres.get('value_pattern')}'"
        )


# ---------------------------------------------------------------------------
# Test: schema show renders enrichment
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestSchemaShow:
    """Validate that schema show displays pattern labels."""

    def test_show_displays_pattern(self, tmux, e2e_target, e2e_semantic_layer):
        """'rdst schema show' output should contain pattern labels."""
        output = tmux.run_rdst(
            f"schema show --target {e2e_target} title_crew",
            timeout=30,
        )
        assert "Pattern:" in output, (
            f"Expected 'Pattern:' in schema show output, got:\n{output[-500:]}"
        )
        assert "comma_separated_list" in output, (
            f"Expected 'comma_separated_list' in schema show output "
            f"for title_crew, got:\n{output[-500:]}"
        )


# ---------------------------------------------------------------------------
# Test: ask uses enrichment for correct SQL
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestAskEnrichment:
    """Test that enriched schema leads to correct SQL for comma-sep columns."""

    def test_directors_column_requires_split(
        self, tmux, e2e_target, e2e_semantic_layer
    ):
        """Counting directors per movie forces splitting the comma-sep column.

        title_crew.directors contains values like "nm001,nm002,nm003".
        The only way to count directors per movie is to split the string.
        Without the [pattern: comma_separated_list] hint, the LLM treats
        it as a scalar and every movie gets count=1.
        """
        output = tmux.run_rdst(
            f'ask --target {e2e_target} --no-interactive '
            f'"Which movies have more than 3 directors? '
            f'Show the movie title and director count"',
            timeout=_LLM_TIMEOUT,
        )

        assert "\nSQL:" in output, (
            f"Expected 'SQL:' in output, got:\n{output[-500:]}"
        )

        sql = _extract_sql(output)
        assert sql, (
            f"Could not extract SQL from output. Full output:\n{output[-1000:]}"
        )

        splits = (
            "STRING_TO_ARRAY" in sql
            or "UNNEST" in sql
            or "SPLIT_PART" in sql
            or "REGEXP_SPLIT" in sql
        )
        assert splits, (
            f"SQL must split the comma-separated directors column to "
            f"count directors, but no splitting function found.\n"
            f"Extracted SQL: {sql}"
        )

    def test_genres_column_requires_split(
        self, tmux, e2e_target, e2e_semantic_layer
    ):
        """Counting per-genre movies forces splitting the comma-sep column.

        title_basics.genres contains values like "Drama,Comedy,Action".
        Listing individual genre counts requires splitting, not
        COUNT(DISTINCT genres) which counts unique combinations.
        """
        output = tmux.run_rdst(
            f'ask --target {e2e_target} --no-interactive '
            f'"List each individual genre and how many movies have that genre, '
            f'splitting apart comma-separated genres"',
            timeout=_LLM_TIMEOUT,
        )

        assert "\nSQL:" in output, (
            f"Expected 'SQL:' in output, got:\n{output[-500:]}"
        )

        sql = _extract_sql(output)
        assert sql, (
            f"Could not extract SQL from output. Full output:\n{output[-1000:]}"
        )

        splits = (
            "STRING_TO_ARRAY" in sql
            or "UNNEST" in sql
            or "SPLIT_PART" in sql
            or "REGEXP_SPLIT" in sql
        )
        assert splits, (
            f"SQL must split the comma-separated genres column, "
            f"but no splitting function found.\n"
            f"Extracted SQL: {sql}"
        )

        assert "JOIN GENRES" not in sql and "FROM GENRES " not in sql, (
            f"SQL incorrectly references a 'genres' table:\n{sql}"
        )
