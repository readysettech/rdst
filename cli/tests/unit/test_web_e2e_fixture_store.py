import json

import pytest

from tests.web_e2e.fixture_store import FixtureStore


def _write_fixtures(tmp_path, operations) -> None:
    (tmp_path / "backend-fixtures.json").write_text(
        json.dumps({"revision": "test-revision", "operations": operations}),
        encoding="utf-8",
    )


def test_fixture_responses_fail_when_exhausted(tmp_path) -> None:
    _write_fixtures(tmp_path, {"operation": [{"value": "once"}]})
    store = FixtureStore(tmp_path)

    assert store.value("operation") == "once"
    with pytest.raises(RuntimeError, match="fixture exhausted"):
        store.value("operation")


def test_fixture_response_can_explicitly_repeat(tmp_path) -> None:
    _write_fixtures(
        tmp_path,
        {"operation": [{"value": "poll-result", "repeat": True}]},
    )
    store = FixtureStore(tmp_path)

    assert store.value("operation") == "poll-result"
    assert store.value("operation") == "poll-result"


def test_none_is_a_supported_default(tmp_path) -> None:
    _write_fixtures(tmp_path, {})
    store = FixtureStore(tmp_path)

    assert store.value("missing", default=None) is None
