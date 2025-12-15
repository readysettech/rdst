"""
Pytest configuration for ask_experimental tests.

These tests are EXCLUDED from normal test runs by default.
To run them explicitly:
    pytest tests/ask_experimental/ -v
"""

import pytest

# Mark all tests in this directory as 'ask_experimental'
def pytest_collection_modifyitems(items):
    for item in items:
        if "ask_experimental" in str(item.fspath):
            item.add_marker(pytest.mark.ask_experimental)
