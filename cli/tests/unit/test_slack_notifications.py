import json
import logging
import time

import pytest
from httpx import ASGITransport, AsyncClient

from shared import slack_notifications as slack
from shared.monitor.monitor_store import MonitorStore

URL = "https://hooks.slack.com/services/T000/B000/secretpart"
# Local noon, outside the default quiet hours.
NOON = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d 12:00:00"), "%Y-%m-%d %H:%M:%S")))


class Response:
    def __init__(self, status_code=200, text="ok"):
        self.status_code, self.text = status_code, text


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv(slack.ENV_NAME, raising=False)
    monkeypatch.delenv(slack.PUBLIC_URL_ENV, raising=False)
    monkeypatch.setattr(slack, "_status", {"last_sent_at": None, "last_error": None,
                                           "last_error_at": None})


@pytest.fixture
def posts(monkeypatch):
    import requests

    sent = []

    def post(url, json=None, timeout=None, allow_redirects=True):
        sent.append({"url": url, "json": json, "timeout": timeout, "redirects": allow_redirects})
        return Response()

    monkeypatch.setattr(requests, "post", post)
    return sent


@pytest.mark.parametrize("url", [
    URL,
    "https://hooks.slack-gov.com/services/T/B/x",
    "https://hooks.slack.com/triggers/T/1/abc",
    "  https://hooks.slack.com/workflows/T/A/1/abc  ",
])
def test_slack_webhook_urls_are_accepted(url):
    assert slack.validate_webhook_url(url) == url.strip()


@pytest.mark.parametrize("url", [
    "http://hooks.slack.com/services/T/B/x",
    "https://hooks.slack.com.evil.example/services/T/B/x",
    "https://evil.example/services/T/B/x",
    "https://user@hooks.slack.com/services/T/B/x",
    "https://hooks.slack.com:8443/services/T/B/x",
    "https://hooks.slack.com/api/chat.postMessage",
    "https://169.254.169.254/services/x",
    "",
])
def test_other_urls_are_refused(url):
    with pytest.raises(slack.InvalidWebhookUrl):
        slack.validate_webhook_url(url)


def event(**overrides):
    row = {"id": 7, "item_id": 3, "ts": NOON, "kind": "notified", "target": "shop",
           "type": "latency_regression", "subject": "q1", "state": "new", "severity": 0.5,
           "title": "Orders lookup got slower", "body_json": json.dumps({}),
           "summary": "Orders lookup is 4x slower than last week"}
    row.update(overrides)
    return row


def test_message_words_a_push_like_the_desktop_app(monkeypatch):
    key, payload = slack.message_for_event(event())
    assert key == "3:7"
    assert payload["text"] == ("*Orders lookup got slower*\n"
                               "shop: Orders lookup is 4x slower than last week")
    monkeypatch.setenv(slack.PUBLIC_URL_ENV, "https://rdst.example.com/")
    _, payload = slack.message_for_event(event(type="stuck_query", title="Query <stuck> & long"))
    assert payload["text"].startswith(":rotating_light: *Query &lt;stuck&gt; &amp; long*")
    assert payload["text"].endswith("<https://rdst.example.com/inbox?item=3|Open in RDST>")


def test_lost_connection_pushes_once_per_episode_and_resumes():
    lost = event(type="monitoring_issue", subject="lost_connection", title="x",
                 body_json=json.dumps({"failed_since": NOON - 60}))
    key, payload = slack.message_for_event(lost)
    assert key == f"3:lost:{NOON - 60}"
    assert payload["text"].startswith(":rotating_light: *Can't reach shop*")
    resumed = dict(lost, state="resolved")
    key, payload = slack.message_for_event(resumed)
    assert key == f"3:resumed:{NOON - 60}"
    assert payload["text"].startswith("*Monitoring resumed on shop*")
    assert slack.message_for_event(dict(lost, ts=NOON - 120)) is None
    assert slack.message_for_event(event(kind="created")) is None


def test_failures_are_reported_without_the_url(monkeypatch, caplog):
    import requests

    def refuse(url, **kwargs):
        raise requests.ConnectionError(f"cannot connect to {url}")

    monkeypatch.setattr(requests, "post", refuse)
    with caplog.at_level(logging.DEBUG):
        error = slack.post(URL, {"text": "hi"})
    assert error == "Slack couldn't be reached (ConnectionError)."
    assert slack.status()["last_error"] == error
    assert "secretpart" not in caplog.text

    monkeypatch.setattr(requests, "post", lambda url, **kwargs: Response(404, "no_service"))
    assert slack.post(URL, {"text": "hi"}) == "Slack answered 404: no_service"


def test_post_uses_a_short_timeout_and_no_redirects(posts):
    assert slack.post(URL, {"text": "hi"}) is None
    assert posts == [{"url": URL, "json": {"text": "hi"}, "timeout": slack.TIMEOUT_SECONDS,
                      "redirects": False}]
    assert slack.status()["last_sent_at"] and slack.status()["last_error"] is None


def test_environment_wins_over_settings(monkeypatch):
    class Store:
        def get_secret(self, name):
            return "https://hooks.slack.com/services/saved"

    monkeypatch.setattr(slack, "_store", lambda: Store())
    assert slack.configured() == ("https://hooks.slack.com/services/saved", "settings")
    monkeypatch.setenv(slack.ENV_NAME, URL)
    assert slack.configured() == (URL, "env")


def push(store, subject, ts):
    store.upsert_item("shop", "stuck_query", subject, 0.9, f"Stuck query {subject}",
                      {"message": f"{subject} has run for 10 minutes"}, ts, delivery="notify")


def test_forwarder_sends_new_pushes_once_and_skips_history(tmp_path, monkeypatch, posts):
    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setenv(slack.ENV_NAME, URL)
    push(store, "before-start", NOON)
    forwarder = slack.SlackForwarder(store)
    assert forwarder.tick() == 0
    push(store, "after-start", NOON + 1)
    assert forwarder.tick() == 1
    assert posts[0]["json"]["text"].startswith(":rotating_light: *Stuck query after-start*")
    assert forwarder.tick() == 0
    assert len(posts) == 1


def test_forwarder_does_nothing_without_a_webhook(tmp_path, monkeypatch, posts):
    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr(slack, "configured", lambda: (None, None))
    forwarder = slack.SlackForwarder(store)
    forwarder.tick()
    push(store, "q", NOON)
    assert forwarder.tick() == 0 and posts == []


@pytest.fixture
def app(monkeypatch):
    from fastapi import FastAPI

    from shared.api.routes import settings

    saved = {}

    class Store:
        def get_secret(self, name):
            return saved.get(name)

        def set_secret(self, name, value, persist=True, apply_to_environment=True):
            assert apply_to_environment is False
            saved[name] = value
            return {"persisted": True}

        def clear_required(self, names, clear_environment="all", update_inventory=True):
            assert clear_environment == "none"
            for name in names:
                saved.pop(name, None)

    monkeypatch.setattr(slack, "_store", lambda: Store())
    monkeypatch.setattr("shared.api.routes.settings.require_local_request", lambda request: None)
    application = FastAPI()
    application.include_router(settings.router, prefix="/api")
    return application, saved


async def test_settings_routes_save_test_and_clear_without_returning_the_url(app, posts):
    application, saved = app
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://t") as client:
        bad = await client.put("/api/settings/slack", json={"webhook_url": "https://evil.example/x"})
        assert bad.status_code == 400 and saved == {}
        response = await client.put("/api/settings/slack", json={"webhook_url": URL})
        assert response.status_code == 200
        assert response.json()["configured"] is True and response.json()["source"] == "settings"
        assert "secretpart" not in response.text
        assert saved == {slack.SECRET_NAME: URL}
        got = await client.get("/api/settings/slack")
        assert "secretpart" not in got.text and got.json()["configured"] is True
        tested = await client.post("/api/settings/slack/test", json={})
        assert tested.json() == {"ok": True, "error": None}
        assert posts[-1]["url"] == URL and "test notification" in posts[-1]["json"]["text"]
        refused = await client.post("/api/settings/slack/test",
                                    json={"webhook_url": "http://hooks.slack.com/services/x"})
        assert refused.status_code == 400 and len(posts) == 1
        cleared = await client.delete("/api/settings/slack")
        assert cleared.json()["configured"] is False and saved == {}
