"""
Unit tests for deploy script generation.

The templates must consume the query_caching variable build_variables
computes; a hardcoded value silently ignores --no-request-path (and its
default), which the integration suite catches much later and slower.
"""

import pytest

from shared.deploy.script_generator import build_variables, generate_script

TARGET_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "testuser",
    "database": "testdb",
    "engine": "postgresql",
}


def _variables(no_request_path: bool) -> dict:
    return build_variables(
        target_name="test-db-pg",
        target_config=TARGET_CONFIG,
        password="testpassword",
        port=5434,
        no_request_path=no_request_path,
    )


@pytest.mark.parametrize("mode", ["docker", "systemd", "kubernetes"])
class TestQueryCachingSubstitution:
    def test_default_is_in_request_path(self, mode):
        script = generate_script(mode, _variables(no_request_path=False))
        assert "in-request-path" in script
        assert 'QUERY_CACHING="explicit"' not in script
        assert "QUERY_CACHING=explicit" not in script

    def test_no_request_path_uses_explicit(self, mode):
        script = generate_script(mode, _variables(no_request_path=True))
        assert "in-request-path" not in script
