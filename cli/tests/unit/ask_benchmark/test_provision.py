import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtools.ask_benchmark import provision
from devtools.ask_benchmark.executor import database_user
from devtools.ask_benchmark.provision import (
    MYSQL_DUMP_ENTRY,
    TABLES_ENTRY,
    ProvisioningError,
    _compose_command,
    _identifier,
    extract_archive,
    provision_mysql,
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


class _MarkerServer:
    """Records statements and keeps the provision marker rows of one MySQL server."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []

    def connect(self, *_args):
        return SimpleNamespace(
            cursor=lambda: _MarkerCursor(self), commit=lambda: None, close=lambda: None
        )


class _MarkerCursor:
    def __init__(self, server):
        self.server = server
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=()):
        self.server.statements.append(sql)
        rows = self.server.rows
        if sql.startswith("SELECT DISTINCT archive_sha256"):
            self.result = [(sha,) for sha in {row[0] for row in rows}]
        elif sql.startswith("SELECT runtime_user_prefix"):
            self.result = [(row[2],) for row in rows if row[1] == params[0]]
        elif sql.startswith("SELECT archive_sha256"):
            self.result = [(row[0],) for row in rows if row[1] == params[0]]
        elif sql.startswith("DELETE FROM `rdst_benchmark_meta`"):
            self.server.rows = [row for row in rows if row[1] != params[0]]
        elif sql.startswith("INSERT INTO `rdst_benchmark_meta`"):
            rows.append(tuple(params))
        elif "information_schema.VIEWS" in sql:
            self.result = [(75,)]
        elif sql == "SELECT VERSION()":
            self.result = [("8.4.0",)]

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


def test_a_new_prefix_adds_its_layout_beside_an_existing_one(tmp_path, monkeypatch):
    server = _MarkerServer([("archive", "bird_", "rdst_bird")])
    monkeypatch.setattr(
        provision, "_load_database_tables", lambda _path: {"financial": ["loan"]}
    )
    monkeypatch.setattr(provision, "start_mysql", lambda **_kwargs: None)
    monkeypatch.setattr(provision, "_admin_connection", server.connect)
    monkeypatch.setattr(provision, "_mysql_image_id", lambda: "image")

    def unexpected_import(*_args, **_kwargs):
        raise AssertionError("The shared BIRD import already holds this archive")

    monkeypatch.setattr(provision, "_import_dump", unexpected_import)

    manifest = provision_mysql(
        tmp_path,
        archive_sha256="archive",
        cache_dir=tmp_path,
        runtime_user="rdst_ask",
        database_prefix="",
    )

    assert server.rows == [("archive", "bird_", "rdst_bird"), ("archive", "", "rdst_ask")]
    assert manifest["database_prefix"] == ""
    assert (tmp_path / "mysql-provision-db-id.json").exists()
    assert "CREATE DATABASE `financial` CHARACTER SET utf8mb4 " in "".join(
        server.statements
    )
    touched_existing = [
        sql
        for sql in server.statements
        if "bird_" in sql or "rdst_bird" in sql or "DROP DATABASE IF EXISTS `BIRD`" in sql
    ]
    assert touched_existing == []
    # The runtime users are shared by every layout, so they gain grants rather than
    # being dropped, which would cut the other layouts' access.
    assert not any(sql.startswith("DROP USER") for sql in server.statements)
    assert any(sql.startswith("CREATE USER IF NOT EXISTS") for sql in server.statements)


def test_layouts_keep_separate_manifests(tmp_path):
    assert provision.provision_manifest_path(
        tmp_path, ""
    ) != provision.provision_manifest_path(tmp_path, "bird_")


@pytest.mark.parametrize("db_id", ["mysql", "information_schema", "sys", "BIRD"])
def test_unprefixed_layout_rejects_server_database_names(db_id):
    with pytest.raises(ProvisioningError, match="replace server databases"):
        provision._reject_reserved_databases({db_id: []}, "")

    provision._reject_reserved_databases({db_id: []}, "bird_")
