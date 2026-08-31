from __future__ import annotations

from features.account import browser_login


class FakeServer:
    def __init__(self, config):
        self.config = config
        self.should_exit = False

    def run(self):
        return None


def test_browser_login_serves_rdst_ui_and_prints_exact_url(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    announced: list[str] = []
    opened: list[str] = []
    prepared: list[bool] = []

    monkeypatch.setattr(browser_login, "_frontend_dist", lambda: tmp_path)
    monkeypatch.setattr(browser_login, "_available_port", lambda: 9123)
    monkeypatch.setattr(browser_login, "_wait_until_ready", lambda _: None)
    monkeypatch.setattr(browser_login, "create_app", lambda static_dist_dir: object())
    monkeypatch.setattr(browser_login.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(browser_login.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        browser_login.account_service,
        "prepare_browser_login",
        lambda: prepared.append(True),
    )
    monkeypatch.setattr(
        browser_login.account_service,
        "wait_for_browser_login",
        lambda timeout: timeout == 3,
    )

    result = browser_login.run_browser_login(
        timeout=3, on_ready=announced.append
    )

    expected = "http://127.0.0.1:9123/account-login"
    assert result == expected
    assert announced == [expected]
    assert opened == [expected]
    assert prepared == [True]
