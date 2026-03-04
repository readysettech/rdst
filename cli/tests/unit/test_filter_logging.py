"""
Unit tests for schema filter logging levels (rdst-2vr.16).

Internal fallback messages in filter.py must NOT emit at WARNING level.
They are expected recovery paths and should be DEBUG only.
"""

import logging

from lib.engines.ask3.phases.filter import _extract_semantic_concepts


class TestFilterLoggingLevels:
    """Fallback paths must not emit warnings visible to users."""

    def test_semantic_extraction_no_response_is_debug(self):
        """When LLM returns empty response, no WARNING should be emitted."""
        logger = logging.getLogger("lib.engines.ask3.phases.filter")
        with _assert_no_warnings(logger):
            result = _extract_semantic_concepts(
                question="How many movies?",
                all_tables=["title_basics"],
                llm_manager=_FakeLLM(response=None),
            )
        assert result["suggested_tables"] == []

    def test_semantic_extraction_bad_json_is_debug(self):
        """When LLM returns unparseable JSON, no WARNING should be emitted."""
        logger = logging.getLogger("lib.engines.ask3.phases.filter")
        with _assert_no_warnings(logger):
            result = _extract_semantic_concepts(
                question="How many movies?",
                all_tables=["title_basics"],
                llm_manager=_FakeLLM(response={"text": "not json at all"}),
            )
        assert result["suggested_tables"] == []

    def test_all_methods_failed_is_debug(self):
        """'All methods failed, using full schema' must not be a WARNING."""
        import inspect
        from lib.engines.ask3.phases import filter as filter_mod

        source = inspect.getsource(filter_mod.filter_schema)
        # The "All methods failed" message must be debug, not warning
        assert 'logger.warning("All methods failed' not in source, (
            "'All methods failed, using full schema' is logged at WARNING — "
            "should be DEBUG (internal fallback, not a user-facing error)"
        )


# -- Helpers ------------------------------------------------------------------

class _FakeLLM:
    """Minimal LLM manager stub that returns a fixed response."""

    def __init__(self, response):
        self._response = response

    def query(self, **kwargs):
        return self._response


class _assert_no_warnings:
    """Context manager that fails if the logger emits WARNING or above."""

    def __init__(self, logger):
        self.logger = logger
        self.handler = _WarningCatcher()

    def __enter__(self):
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        if self.handler.warnings:
            raise AssertionError(
                f"Unexpected WARNING log(s): {self.handler.warnings}"
            )
        return False


class _WarningCatcher(logging.Handler):
    """Captures WARNING+ records."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.warnings = []

    def emit(self, record):
        self.warnings.append(record.getMessage())
