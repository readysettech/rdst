"""Server mode: ``rdst web`` running as a long-lived service in a container.

The desktop app and a local ``rdst web`` are protected by listening on loopback.
A container is reached through a published port or a Kubernetes ingress, so
server mode adds what that exposure needs:

- an access token that every request must carry (a session cookie set by the
  sign-in page, or ``Authorization: Bearer``), unless ``RDST_AUTH=none`` says a
  proxy in front already authenticates people;
- one agent key for coding agents on other machines, accepted only by /mcp (see
  shared.agent_key);
- a file-backed keyring under the data directory, so secrets entered in the UI
  survive restarts where no OS keychain exists;
- origin checks that honor ``X-Forwarded-Proto``/``X-Forwarded-Host`` from a
  TLS-terminating ingress;
- one instance per data directory, so leases left by an earlier container are
  reclaimed at once;
- no features that start their own containers (Readyset comparisons, the
  caching demo, cache deploys), since a container can't run Docker.

Environment:
    RDST_SERVER=1          turn server mode on (the container image sets it)
    RDST_AUTH=token|none   token (default) or none
    RDST_ACCESS_TOKEN      the access token; generated into
                           ``$RDST_DATA_DIR/access-token`` when unset
"""

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from shared.constants import rdst_data_dir

SESSION_COOKIE = "rdst_session"
SESSION_MAX_AGE_SECONDS = 90 * 24 * 3600
_PUBLIC_PATHS = {"/health", "/login", "/logout"}
_FAILED_SIGN_IN_DELAY_SECONDS = 1.0
_SIGN_IN_FORM_MAX_BYTES = 4096
# Marks the access check's own 401, so the UI tells it apart from a route's 401.
SIGN_IN_HEADER = "X-RDST-Sign-In"

_token_lock = threading.Lock()
_token_cache: tuple[str, str] | None = None


def enabled() -> bool:
    return os.environ.get("RDST_SERVER", "").strip().lower() in {"1", "true", "yes"}


def auth_required() -> bool:
    return enabled() and os.environ.get("RDST_AUTH", "token").strip().lower() != "none"


def access_token_path() -> Path:
    return rdst_data_dir() / "access-token"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def access_token() -> tuple[str, str]:
    """Return ``(token, source)``; source is ``env``, ``file`` or ``generated``."""
    global _token_cache
    with _token_lock:
        if _token_cache is not None:
            return _token_cache
        from_env = os.environ.get("RDST_ACCESS_TOKEN", "").strip()
        if from_env:
            _token_cache = (from_env, "env")
            return _token_cache
        path = access_token_path()
        try:
            stored = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            stored = ""
        if stored:
            _token_cache = (stored, "file")
        else:
            generated = secrets.token_urlsafe(24)
            _write_private(path, generated + "\n")
            _token_cache = (generated, "generated")
        return _token_cache


def _session_value(token: str) -> str:
    return hmac.new(token.encode(), b"rdst-session-v1", hashlib.sha256).hexdigest()


def _token_matches(candidate: str) -> bool:
    token, _ = access_token()
    return bool(candidate) and hmac.compare_digest(candidate.encode(), token.encode())


def _authenticated(request: Any) -> bool:
    token, _ = access_token()
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie and hmac.compare_digest(cookie.encode(), _session_value(token).encode()):
        return True
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return _token_matches(header[7:].strip())
    return False


def bearer_authenticated(request: Any) -> bool:
    """A request carrying the access token itself. Browsers never attach this
    header on their own, so such a request can't be a cross-site forgery."""
    if not auth_required():
        return False
    header = request.headers.get("authorization", "")
    return header.lower().startswith("bearer ") and _token_matches(header[7:].strip())


def agent_key_authenticated(request: Any) -> bool:
    """A request carrying this server's agent key (see shared.agent_key). Only /mcp
    accepts it; everywhere else it counts as no credentials at all."""
    if not enabled():
        return False
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    from shared import agent_key

    return agent_key.matches(header[7:].strip())


def _first_forwarded(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def forwarded_scheme_and_host(request: Any) -> tuple[str, str | None]:
    """The scheme and Host the browser used, behind a TLS-terminating proxy."""
    host = request.headers.get("host")
    proto = ""
    if enabled():
        proto = _first_forwarded(request.headers.get("x-forwarded-proto")).lower()
        forwarded_host = _first_forwarded(request.headers.get("x-forwarded-host"))
        if forwarded_host:
            host = forwarded_host
    if not host:
        return "http", None
    return (proto if proto in {"http", "https"} else request.url.scheme), host


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def _set_session_cookie(response: Any, request: Any) -> None:
    token, _ = access_token()
    scheme, _ = forwarded_scheme_and_host(request)
    response.set_cookie(
        SESSION_COOKIE,
        _session_value(token),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=scheme == "https",
        path="/",
    )


_SIGN_IN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in - RDST</title>
<style>
:root {{ color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --fg:#14161a; --muted:#5b6270;
  --border:#d9dde3; --accent:#2f6fed; --err:#c62828; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#171a20; --fg:#e8eaee;
  --muted:#9aa3b2; --border:#2a2f38; --accent:#5b8cff; --err:#ff6b6b; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
  background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
  padding:16px; }}
main {{ width:100%; max-width:400px; background:var(--card); border:1px solid var(--border);
  border-radius:12px; padding:28px; }}
h1 {{ font-size:20px; margin:0 0 6px; }}
p {{ margin:0 0 18px; color:var(--muted); }}
label {{ display:block; font-weight:600; margin-bottom:6px; }}
input {{ width:100%; padding:10px 12px; border:1px solid var(--border); border-radius:8px;
  background:transparent; color:inherit; font:inherit; }}
button {{ margin-top:16px; width:100%; padding:10px; border:0; border-radius:8px;
  background:var(--accent); color:#fff; font:inherit; font-weight:600; cursor:pointer; }}
.err {{ color:var(--err); margin:12px 0 0; }}
code {{ font-size:13px; word-break:break-all; }}
details {{ margin:-8px 0 18px; color:var(--muted); font-size:14px; }}
summary {{ cursor:pointer; }}
dl {{ margin:8px 0 0; }}
dt {{ font-weight:600; color:var(--fg); margin-top:6px; }}
dd {{ margin:2px 0 0; }}
</style></head>
<body><main>
<h1>Sign in to RDST</h1>
<p>Enter this server's access token.</p>
<details><summary>Where to find it</summary>
<p><code>{where}</code></p></details>
<form method="post" action="/login">
<input type="hidden" name="next" value="{next}">
<label for="token">Access token</label>
<input id="token" name="token" type="password" autocomplete="current-password" autofocus required>
<button type="submit">Sign in</button>
{error}
</form>
</main></body></html>
"""


def token_command() -> str:
    """The command that shows the access token: kubectl for the Helm chart, else docker."""
    secret = os.environ.get("RDST_AUTH_SECRET", "").strip()
    if secret:
        key = os.environ.get("RDST_AUTH_SECRET_KEY", "").strip() or "access-token"
        namespace = os.environ.get("RDST_NAMESPACE", "").strip()
        scope = f"-n {namespace} " if namespace else ""
        return (f"kubectl {scope}get secret {secret} -o jsonpath='{{.data.{key}}}' "
                "| base64 -d")
    return f"docker exec <container> cat {access_token_path()}"


def _sign_in_page(next_path: str, error: str = "") -> str:
    error_html = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    return _SIGN_IN_PAGE.format(next=html.escape(next_path, quote=True), error=error_html,
                                where=html.escape(token_command(), quote=False))


def install(app: Any) -> None:
    """Add the sign-in routes and the access check to the FastAPI app."""
    if not auth_required():
        return

    from fastapi import Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    @app.get("/login", include_in_schema=False)
    async def sign_in_page(request: Request):
        if _authenticated(request):
            return RedirectResponse(_safe_next(request.query_params.get("next")), 303)
        return HTMLResponse(_sign_in_page(_safe_next(request.query_params.get("next"))),
                            headers={"Cache-Control": "no-store"})

    @app.post("/login", include_in_schema=False)
    async def sign_in(request: Request):
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > _SIGN_IN_FORM_MAX_BYTES:
                return HTMLResponse("Sign-in form too large.", status_code=413)
        form = parse_qs(body.decode("utf-8", "replace"))
        next_path = _safe_next((form.get("next") or ["/"])[0])
        if not _token_matches((form.get("token") or [""])[0].strip()):
            await asyncio.sleep(_FAILED_SIGN_IN_DELAY_SECONDS)
            return HTMLResponse(_sign_in_page(next_path, "That token doesn't match."),
                                status_code=401, headers={"Cache-Control": "no-store"})
        response = RedirectResponse(next_path, 303)
        _set_session_cookie(response, request)
        return response

    @app.get("/logout", include_in_schema=False)
    async def sign_out():
        response = RedirectResponse("/login", 303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.middleware("http")
    async def require_access_token(request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or _authenticated(request):
            return await call_next(request)
        if path == "/mcp" and agent_key_authenticated(request):
            return await call_next(request)
        if path.startswith("/api/") or path in ("/api", "/mcp"):
            return JSONResponse(
                status_code=401,
                content={"code": "unauthorized", "detail": "Sign in to this RDST server.",
                         "message": "Sign in to this RDST server."},
                headers={SIGN_IN_HEADER: "required"},
            )
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='')}", 303)


def install_agent_key(app: Any) -> None:
    """Routes the database page uses to create, show and revoke the agent key.

    They sit behind the access-token check like the rest of /api, and the agent key itself
    is never accepted here, so an agent can't mint or revoke keys.
    """
    if not enabled():
        return

    from fastapi import Request

    from shared import agent_key
    from shared.api.guards import require_local_request

    @app.get("/api/server/agent-key", include_in_schema=False)
    async def agent_key_status(request: Request):
        return agent_key.status()

    @app.post("/api/server/agent-key", include_in_schema=False)
    async def create_agent_key(request: Request):
        require_local_request(request)
        key = agent_key.create()
        return {"key": key, **agent_key.status()}

    @app.delete("/api/server/agent-key", include_in_schema=False)
    async def revoke_agent_key(request: Request):
        require_local_request(request)
        return {"revoked": agent_key.revoke(), **agent_key.status()}


class FileKeyring:
    """A keyring backend that keeps secrets in a 0600 JSON file on the data volume.

    Containers have no OS keychain. This keeps the same secret-store code path
    working, with the file's protection coming from its permissions and from
    the volume it lives on.
    """

    priority = 1

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}

    def get_password(self, service: str, username: str) -> str | None:
        with self._lock:
            value = self._load().get(service, {}).get(username)
        return value if isinstance(value, str) else None

    def set_password(self, service: str, username: str, password: str) -> None:
        with self._lock:
            data = self._load()
            data.setdefault(service, {})[username] = password
            _write_private(self.path, json.dumps(data, indent=1, sort_keys=True))

    def delete_password(self, service: str, username: str) -> None:
        from keyring.errors import PasswordDeleteError

        with self._lock:
            data = self._load()
            if username not in data.get(service, {}):
                raise PasswordDeleteError(username)
            del data[service][username]
            _write_private(self.path, json.dumps(data, indent=1, sort_keys=True))


def secrets_path() -> Path:
    return rdst_data_dir() / "secrets.json"


def activate() -> None:
    """Point the keyring at the data volume. Safe to call more than once."""
    if not enabled():
        return
    try:
        import keyring
        from keyring.backend import KeyringBackend
    except Exception:
        return

    class RdstFileKeyring(FileKeyring, KeyringBackend):
        def __init__(self, path: Path):
            FileKeyring.__init__(self, path)
            KeyringBackend.__init__(self)

    current = keyring.get_keyring()
    if type(current).__name__ != "RdstFileKeyring":
        keyring.set_keyring(RdstFileKeyring(secrets_path()))


def startup_lines(host: str, port: int) -> list[str]:
    """What the container log says about reaching and signing in to the server."""
    lines = [f"RDST is running in server mode on port {port}."]
    if not auth_required():
        lines.append("Sign-in is off (RDST_AUTH=none); put an authenticating proxy in front.")
        return lines
    _, source = access_token()
    if os.environ.get("RDST_AUTH_SECRET", "").strip():
        lines.append("The access token is in a Kubernetes Secret. Show it with:")
        lines.append(f"  {token_command()}")
        return lines
    if source == "env":
        lines.append("The access token comes from RDST_ACCESS_TOKEN.")
        return lines
    lines.append(f"The access token is saved in {access_token_path()} on the data volume. "
                 "Show it with:")
    lines.append(f"  {token_command()}")
    lines.append("Set RDST_ACCESS_TOKEN to choose your own.")
    return lines


class _HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        return not (len(args) >= 3 and str(args[2]).split("?", 1)[0] == "/health")


def quiet_health_checks() -> None:
    """Keep liveness and readiness probes out of the access log."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _HealthCheckFilter) for f in access.filters):
        access.addFilter(_HealthCheckFilter())


NETWORK_HINT = ("If RDST runs in Kubernetes, a NetworkPolicy, firewall or security group may be "
                "blocking it; with the RDST chart, allow the database in networkPolicy.databases.")


def network_hint() -> str:
    """Extra advice for unreachable databases when RDST runs as a server."""
    return f" {NETWORK_HINT}" if enabled() else ""


CONTAINERS_UNAVAILABLE = (
    "This RDST server runs in a container, so it can't start Readyset or demo "
    "containers. Readyset comparisons and the caching demo run in the RDST "
    "desktop app: https://readyset.io/downloads"
)


def containers_unavailable_reason() -> str | None:
    """Why features that start Docker containers are off, or None when they're on."""
    return CONTAINERS_UNAVAILABLE if enabled() else None


def require_containers() -> None:
    """Refuse an API request that would start a container on this server."""
    if enabled():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={"code": "unavailable_in_container", "message": CONTAINERS_UNAVAILABLE},
        )


LOCAL_APPS_UNAVAILABLE = (
    "This RDST server runs in a container, so it can't run a coding agent, open "
    "a terminal or an app, or change an agent's settings on your computer. To "
    "use Claude Code or Codex with this server, open a database and use Connect "
    "Claude or Codex to RDST, which gives them an agent key."
)


def require_local_apps() -> None:
    """Refuse a request that would start or configure an app on the server's machine.

    The person using a server is in a browser elsewhere, so a coding agent, terminal
    or desktop app started here would run in the container, out of their reach.
    """
    if enabled():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={"code": "unavailable_in_server_mode", "message": LOCAL_APPS_UNAVAILABLE},
        )


SERVER_FILES_UNAVAILABLE = (
    "This RDST server doesn't read files from its own disk. Upload the file from "
    "your browser instead."
)


def require_local_files() -> None:
    """Refuse a request that names a path on the server's disk.

    The person using a server is in a browser elsewhere; their files never reach this
    disk, and the server's own files aren't theirs to browse.
    """
    if enabled():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={"code": "unavailable_in_server_mode", "message": SERVER_FILES_UNAVAILABLE},
        )


def index_marker() -> str:
    """Tag served into index.html so the UI can adapt to a server deployment."""
    return '<meta name="rdst-runtime" content="container">' if enabled() else ""


def reclaim_foreign_leases() -> bool:
    """One server owns its data directory, so another host's lease is stale."""
    return enabled()


BROKER_SIGN_IN_LOCALHOST_ONLY = (
    "Readyset, Supabase and DigitalOcean sign-in on an RDST server finish at "
    "localhost for now. Open RDST through kubectl port-forward or its published "
    "Docker port at http://localhost to sign in; everything else works at this "
    "address."
)


def broker_sign_in_refusal(request: Any) -> str | None:
    """Why a keyservice sign-in can't start from this browser, or None when it can.

    The keyservice returns sign-ins only to localhost, so a server opened at any other
    host can't finish one.
    """
    if not enabled():
        return None
    origin = public_origin(request)
    host = urlsplit(origin).hostname if origin else None
    return None if host in ("localhost", "127.0.0.1") else BROKER_SIGN_IN_LOCALHOST_ONLY


def public_origin(request: Any) -> str | None:
    scheme, host = forwarded_scheme_and_host(request)
    if not host:
        return None
    try:
        parsed = urlsplit(f"{scheme}://{host}")
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    return f"{scheme}://{parsed.netloc}"
