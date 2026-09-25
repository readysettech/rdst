"""Startup markers the desktop shell reads while ``rdst web`` upgrades the data directory."""

import json
import os

import pytest

from shared import startup_status
from shared.monitor import monitor_store
from tests.unit.test_upgrade_path import build_old_install, start_app


def _markers(text: str) -> list[dict]:
    prefix = startup_status.MARKER + " "
    return [json.loads(line[len(prefix):]) for line in text.splitlines()
            if line.startswith(prefix)]


@pytest.fixture
def markers_on(monkeypatch):
    monkeypatch.setenv(startup_status.ENV_VAR, "1")
    monkeypatch.setattr(startup_status, "_active", 0)


def test_markers_stay_off_until_enabled(monkeypatch, capsys):
    monkeypatch.delenv(startup_status.ENV_VAR, raising=False)
    with startup_status.migrating("library.db"):
        pass
    assert capsys.readouterr().out == ""

    startup_status.enable()
    try:
        assert startup_status.enabled()
    finally:
        os.environ.pop(startup_status.ENV_VAR, None)


def test_migrating_is_paired_with_migrated_and_counts_overlaps(markers_on, capsys):
    startup_status.emit("starting")
    with startup_status.migrating("library.db"):
        with startup_status.migrating("cache.db"):
            pass
    with pytest.raises(RuntimeError):
        with startup_status.migrating("monitor.db"):
            raise RuntimeError("step failed")

    assert _markers(capsys.readouterr().out) == [
        {"state": "starting"},
        {"state": "migrating", "store": "library.db"},
        {"state": "migrating", "store": "cache.db"},
        {"state": "migrated", "store": "cache.db", "pending": 1},
        {"state": "migrated", "store": "library.db", "pending": 0},
        {"state": "migrating", "store": "monitor.db"},
        {"state": "migrated", "store": "monitor.db", "pending": 0},
    ]


def test_upgrade_reports_each_migration_once_and_a_second_start_reports_none(
    tmp_path, monkeypatch, capsys, markers_on
):
    data = tmp_path / ".rdst"
    monkeypatch.setenv("RDST_DATA_DIR", str(data))
    monkeypatch.setattr(monitor_store, "_OPENED", set())
    build_old_install(data, monkeypatch)
    capsys.readouterr()

    start_app(monkeypatch)
    seen = _markers(capsys.readouterr().out)
    started = [marker["store"] for marker in seen if marker["state"] == "migrating"]
    assert "library.db" in started and "monitor.db" in started
    finished = [marker["store"] for marker in seen if marker["state"] == "migrated"]
    assert sorted(finished) == sorted(started)
    assert seen[-1]["pending"] == 0

    monkeypatch.setattr(monitor_store, "_OPENED", set())
    start_app(monkeypatch)
    assert _markers(capsys.readouterr().out) == []
