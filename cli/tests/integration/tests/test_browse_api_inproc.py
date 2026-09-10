"""In-process integration tests for the /api/browse endpoint.

Covers directory listing, the ext filter used by the file picker UI, and the
home-directory flag the picker uses to refuse a one-click whole-home scan.
"""

from __future__ import annotations

import os

from shared.api.routes import browse


def test_windows_drive_entries_include_available_drives(monkeypatch):
    monkeypatch.setattr(
        browse.os.path,
        "exists",
        lambda path: path in {"C:\\", "E:\\"},
    )

    assert [entry.path for entry in browse._windows_drive_entries()] == [
        "C:\\",
        "E:\\",
    ]


async def test_browse_lists_directories_only_by_default(client, tmp_path):
    root = tmp_path / "browse"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "fleet.csv").write_text("name,host,engine\n")

    response = await client.get(f"/api/browse?path={root}")
    assert response.status_code == 200
    body = response.json()
    assert body["current"] == str(root)
    assert [d["name"] for d in body["directories"]] == ["sub"]
    assert body["files"] == []


async def test_browse_ext_filter_lists_matching_files(client, tmp_path):
    root = tmp_path / "browse"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "fleet.csv").write_text("name,host,engine\n")
    (root / "UPPER.CSV").write_text("name,host,engine\n")
    (root / "notes.txt").write_text("x\n")
    (root / ".hidden.csv").write_text("x\n")

    response = await client.get(f"/api/browse?path={root}&ext=csv")
    assert response.status_code == 200
    body = response.json()
    assert [d["name"] for d in body["directories"]] == ["sub"]
    assert [f["name"] for f in body["files"]] == ["fleet.csv", "UPPER.CSV"]
    assert all(f["path"].startswith(str(root)) for f in body["files"])


async def test_browse_ext_accepts_leading_dot(client, tmp_path):
    root = tmp_path / "browse"
    root.mkdir()
    (root / "fleet.csv").write_text("name,host,engine\n")

    response = await client.get(f"/api/browse?path={root}&ext=.csv")
    assert response.status_code == 200
    assert [f["name"] for f in response.json()["files"]] == ["fleet.csv"]


async def test_browse_rejects_missing_path(client, tmp_path):
    response = await client.get(f"/api/browse?path={tmp_path}/nope")
    assert response.status_code == 400


async def test_browse_without_a_path_starts_in_the_working_directory(
    client, tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    response = await client.get("/api/browse")
    assert response.status_code == 200
    body = response.json()
    assert body["current"] == os.path.abspath(str(project))
    assert body["is_home"] is False


async def test_browse_flags_the_home_directory(client, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    response = await client.get(f"/api/browse?path={home}")
    assert response.status_code == 200
    assert response.json()["is_home"] is True
