"""The preferences store behind Settings: theme, font size, how long readings stay, the
selected database and push notifications."""

from __future__ import annotations

import toml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.api.routes import settings as settings_mod
from shared.monitor import monitor_store


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(settings_mod.router, prefix="/api")
    return TestClient(app, client=("127.0.0.1", 54321))


def test_preferences_default_persist_and_feed_the_reading_retention(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/settings/preferences").json() == {
        "theme": "dark", "font_size": "default", "keep_readings_days": 7, "report_view": "quick",
        "selected_target": None, "push_notifications": None}
    updated = client.put("/api/settings/preferences",
                         json={"theme": "light", "keep_readings_days": 30}).json()
    assert updated == {"theme": "light", "font_size": "default", "keep_readings_days": 30,
                       "report_view": "quick", "selected_target": None,
                       "push_notifications": None}
    assert client.get("/api/settings/preferences").json() == updated
    saved = toml.load(tmp_path / "config.toml")["preferences"]
    assert saved == {key: value for key, value in updated.items() if value is not None}
    preferences = monitor_store.read_preferences(tmp_path / "config.toml")
    assert monitor_store.keep_readings_days(preferences) == 30
    assert monitor_store.reading_retention(preferences)[300] == 30 * 86400
    assert monitor_store.reading_retention({})[300] == 7 * 86400
    assert monitor_store.keep_readings_days({"keep_readings_days": 12}) == 7
    assert client.put("/api/settings/preferences", json={"theme": "sepia"}).status_code == 422
    assert client.put("/api/settings/preferences",
                      json={"keep_readings_days": 12}).status_code == 422
    assert client.put("/api/settings/preferences", json={"font_size": "large"}).json()[
        "font_size"] == "large"
    # The Report page's view is remembered per user, quick until someone picks detailed.
    assert client.put("/api/settings/preferences", json={"report_view": "detailed"}).json()[
        "report_view"] == "detailed"
    assert client.get("/api/settings/preferences").json()["theme"] == "light"
    assert client.put("/api/settings/preferences", json={"report_view": "full"}).status_code == 422


def _configure(*names: str) -> None:
    from shared.config.targets import TargetsConfig

    cfg = TargetsConfig()
    cfg.load()
    for name in names:
        cfg.upsert(name, {"engine": "postgresql", "host": "localhost", "port": 5432,
                          "user": "app", "database": "app"})
    cfg.save()


def test_preferences_keep_the_selected_database_and_push_choice(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _configure("prod-db")
    url = "/api/settings/preferences"
    chosen = client.put(url, json={"selected_target": " prod-db ", "push_notifications": True})
    assert chosen.json()["selected_target"] == "prod-db"
    assert chosen.json()["push_notifications"] is True
    # Another change leaves both alone; they survive a fresh read of the file.
    client.put(url, json={"theme": "system"})
    again = client.get(url).json()
    assert (again["selected_target"], again["push_notifications"]) == ("prod-db", True)
    assert toml.load(tmp_path / "config.toml")["preferences"]["selected_target"] == "prod-db"
    cleared = client.put(url, json={"selected_target": "", "push_notifications": False}).json()
    assert (cleared["selected_target"], cleared["push_notifications"]) == (None, False)
    assert "selected_target" not in toml.load(tmp_path / "config.toml")["preferences"]
    assert client.put(url, json={"selected_target": "x" * 257}).status_code == 422


def test_a_removed_database_is_no_longer_the_selection(monkeypatch, tmp_path):
    from shared.config.targets import TargetsConfig

    client = _client(monkeypatch, tmp_path)
    _configure("prod-db")
    url = "/api/settings/preferences"
    client.put(url, json={"selected_target": "prod-db"})
    cfg = TargetsConfig()
    cfg.load()
    cfg.remove("prod-db")
    cfg.save()
    assert client.get(url).json()["selected_target"] is None
