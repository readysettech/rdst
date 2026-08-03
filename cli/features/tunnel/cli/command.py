"""SSH tunnel command orchestration."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared.cli.types import RdstResult
from shared.config.targets import TargetsConfig
from shared.ui import DataTable, get_console


NO_WEB_SERVER_MESSAGE = (
    "CLI tunnels only exist during each command; run `rdst web` to hold "
    "long-lived tunnels."
)


class TunnelCommand:
    """Inspect, close, and test in-process SSH tunnels."""

    def __init__(
        self,
        manager: Any = None,
        config: Optional[TargetsConfig] = None,
        console: Any = None,
        web_port: Optional[int] = None,
    ):
        if manager is None:
            from shared.ssh_tunnel import get_tunnel_manager

            manager = get_tunnel_manager()
        self.manager = manager
        self.config = config
        self.console = console or get_console()
        self.web_port = web_port

    def execute(self, subcommand: str, **kwargs) -> RdstResult:
        handler = getattr(self, f"_handle_{subcommand.replace('-', '_')}", None)
        if handler is None:
            return RdstResult(False, f"Unknown tunnel subcommand: {subcommand}")
        return handler(**kwargs)

    def _handle_list(self, **kwargs) -> RdstResult:
        response = self._web_request("status")
        if response is None:
            self.console.print(NO_WEB_SERVER_MESSAGE)
            return RdstResult(True, "")
        if isinstance(response, dict) and response.get("error"):
            return RdstResult(False, str(response["error"]))

        tunnels = response
        if not tunnels:
            self.console.print("The RDST web server is not holding any SSH tunnels.")
            return RdstResult(True, "")

        rows = []
        for tunnel in tunnels:
            last_used = datetime.fromtimestamp(tunnel["last_used"]).astimezone()
            state = "active" if tunnel.get("state") == "active" else "dead"
            rows.append(
                (
                    tunnel["target"],
                    tunnel["jump_host"],
                    str(tunnel["local_port"]),
                    state,
                    last_used.strftime("%Y-%m-%d %H:%M:%S %Z"),
                )
            )
        self.console.print(
            DataTable(
                columns=["Target", "Jump host", "Local port", "State", "Last used"],
                rows=rows,
                title="Tunnels held by the RDST web server",
            )
        )
        return RdstResult(True, "")

    def _handle_close(
        self,
        target: Optional[str] = None,
        close_all: bool = False,
        **kwargs,
    ) -> RdstResult:
        if close_all and target:
            return RdstResult(False, "Specify a target or --all, not both")
        if not target:
            if not close_all:
                return RdstResult(False, "Target required: rdst tunnel close <target>")

        response = self._web_request(
            "close",
            payload={"all": close_all, "target": target},
        )
        if response is None:
            self.console.print(NO_WEB_SERVER_MESSAGE)
            return RdstResult(True, "")
        if isinstance(response, dict) and response.get("error"):
            return RdstResult(False, str(response["error"]))

        message = response.get("message") if isinstance(response, dict) else None
        self.console.print(message or "The RDST web server closed the requested tunnel.")
        return RdstResult(True, "")

    def _web_request(
        self,
        path: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> Any:
        port = self.web_port
        if port is None:
            try:
                port = int(os.environ.get("RDST_WEB_PORT", "8787"))
            except ValueError:
                port = 8787
        url = f"http://127.0.0.1:{port}/api/tunnel/{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method="POST" if body else "GET",
        )
        try:
            with urlopen(request, timeout=0.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                detail = None
            return {"error": detail or f"RDST web server returned HTTP {exc.code}."}
        except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
            return None

    def _handle_test(self, target: str, **kwargs) -> RdstResult:
        cfg = self.config or TargetsConfig()
        if self.config is None:
            cfg.load()
        target_config = cfg.get(target)
        if target_config is None:
            return RdstResult(
                False,
                f"Target '{target}' not found. Run 'rdst configure list' to see targets.",
            )
        if not target_config.get("ssh"):
            return RdstResult(
                False,
                f"Target '{target}' has no SSH jump host. "
                f"Run 'rdst configure edit {target}' to add one.",
            )

        from shared.ssh_tunnel import SshPassphraseRequired

        # A test is a validation operation, not a status check. Discard any
        # cached path before opening the configuration that is being tested.
        self.manager.close(target)
        try:
            from shared.db_connection import (
                probe_target_connection,
                resolve_connection_params,
            )

            params = resolve_connection_params(
                target=target,
                target_config=target_config,
            )
        except SshPassphraseRequired as exc:
            import getpass

            key_path = target_config["ssh"].get("key_path", "SSH key")
            passphrase = getpass.getpass(f"Passphrase for {key_path}: ")
            if not passphrase:
                return self._ssh_failure(exc, target, target_config["ssh"])
            try:
                self.manager.ensure_tunnel(
                    target,
                    target_config["ssh"],
                    str(target_config["host"]),
                    int(target_config["port"]),
                    passphrase=passphrase,
                )
                params = resolve_connection_params(
                    target=target,
                    target_config=target_config,
                )
            except Exception as retry_exc:
                self.manager.close(target)
                return self._ssh_failure(retry_exc, target, target_config["ssh"])
        except Exception as exc:
            return self._ssh_failure(exc, target, target_config["ssh"])

        probe_config = dict(target_config)
        probe_config.pop("ssh", None)
        probe_config.update(
            {
                "engine": params["engine"],
                "host": params["host"],
                "port": params["port"],
                "user": params["user"],
                "password": params["password"],
                "database": params["database"],
                "tls": params["tls"],
                "tls_verify": params["tls_verify"],
                "tls_ca": params["tls_ca"],
            }
        )
        if params.get("hostaddr"):
            probe_config["hostaddr"] = params["hostaddr"]
        state = probe_target_connection(probe_config, connect_timeout=5)
        if state["success"]:
            self.console.print(
                f"Tunnel test passed for '{target}': jump host and database are reachable."
            )
            return RdstResult(True, "")

        self.manager.close(target)
        message = (
            f"SSH tunnel opened for '{target}', but the database is unreachable "
            f"through it: {state.get('error') or 'connection failed'}. "
            "Check the database host, port, security group, and DB credentials."
        )
        return RdstResult(False, message, data=state)

    @staticmethod
    def _ssh_failure(exc: Exception, target: str, ssh_config: dict) -> RdstResult:
        from shared.api.ssh_errors import ssh_error_payload

        message = ssh_error_payload(exc, target, ssh_config)["message"]
        return RdstResult(False, message)
