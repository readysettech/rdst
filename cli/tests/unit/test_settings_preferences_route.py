"""The preferences store behind Settings: theme, font size and how long readings stay."""

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
        "theme": "dark", "font_size": "default", "keep_readings_days": 7, "report_view": "quick"}
    updated = client.put("/api/settings/preferences",
                         json={"theme": "light", "keep_readings_days": 30}).json()
    assert updated == {"theme": "light", "font_size": "default", "keep_readings_days": 30,
                       "report_view": "quick"}
    assert client.get("/api/settings/preferences").json() == updated
    saved = toml.load(tmp_path / "config.toml")["preferences"]
    assert saved == updated
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
