from __future__ import annotations

import webbrowser
from unittest import mock

import rdst


class TestOpenUrlInBrowser:

    def test_webbrowser_success_needs_no_fallback(self):
        with mock.patch.object(webbrowser, "open", return_value=True) as opened, \
             mock.patch("subprocess.run") as run:
            rdst._open_url_in_browser("http://127.0.0.1:8787")
        opened.assert_called_once()
        run.assert_not_called()

    def test_non_wsl_stays_silent_when_no_browser(self):
        with mock.patch.object(webbrowser, "open", return_value=False), \
             mock.patch.object(rdst, "_running_under_wsl", return_value=False), \
             mock.patch("subprocess.run") as run:
            rdst._open_url_in_browser("http://127.0.0.1:8787")
        run.assert_not_called()

    def test_wsl_falls_back_to_windows_default_browser(self):
        with mock.patch.object(webbrowser, "open", return_value=False), \
             mock.patch.object(rdst, "_running_under_wsl", return_value=True), \
             mock.patch("subprocess.run") as run:
            rdst._open_url_in_browser("http://127.0.0.1:8787")
        assert run.call_count == 1
        assert run.call_args[0][0][0] == "wslview"

    def test_wsl_tries_windows_explorer_when_wslview_missing(self):
        with mock.patch.object(webbrowser, "open", return_value=False), \
             mock.patch.object(rdst, "_running_under_wsl", return_value=True), \
             mock.patch("subprocess.run",
                        side_effect=[FileNotFoundError(), mock.Mock()]) as run:
            rdst._open_url_in_browser("http://127.0.0.1:8787")
        assert run.call_count == 2
        assert run.call_args_list[1][0][0] == [
            "explorer.exe",
            "http://127.0.0.1:8787",
        ]
