import json
import zipfile
from pathlib import Path

import pytest

from devtools.ask_benchmark.executor import database_user
from devtools.ask_benchmark.provision import (
    MYSQL_DUMP_ENTRY,
    TABLES_ENTRY,
    ProvisioningError,
    _compose_command,
    _identifier,
    extract_archive,
)


def test_compose_project_can_be_isolated_per_ci_job(monkeypatch):
    monkeypatch.setenv("BIRD_MYSQL_COMPOSE_PROJECT", "rdst-bird-ci-job-123")

    command = _compose_command("up", "-d")

    assert command[command.index("-p") + 1] == "rdst-bird-ci-job-123"


def test_extract_archive_selects_required_entries(tmp_path: Path):
    archive = tmp_path / "mini.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(MYSQL_DUMP_ENTRY, "SELECT 1;")
        output.writestr(TABLES_ENTRY, json.dumps([]))
        output.writestr(
            "minidev/MINIDEV/dev_databases/db/database_description/table.csv",
            "original_column_name,column_description\nid,identifier\n",
        )
        output.writestr("minidev/unrelated.txt", "ignored")

    extracted, digest = extract_archive(archive, tmp_path / "cache")

    assert len(digest) == 64
    dump = extracted / MYSQL_DUMP_ENTRY
    assert dump.read_text() == "SELECT 1;"
    assert not (extracted / "minidev/unrelated.txt").exists()

    dump.unlink()
    reused, _ = extract_archive(archive, tmp_path / "cache")
    assert (reused / MYSQL_DUMP_ENTRY).read_text() == "SELECT 1;"


def test_database_users_are_isolated_and_bounded():
    first = database_user("rdst_bird", "financial")
    second = database_user("rdst_bird", "card_games")

    assert first != second
    assert len(first) <= 32
    assert first == database_user("rdst_bird", "financial")


def test_identifier_rejects_sql_syntax():
    with pytest.raises(ProvisioningError, match="Unsafe"):
        _identifier("db`; DROP DATABASE BIRD")
