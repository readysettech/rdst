"""
Agent HTTP Server

Provides an HTTP API for data agents using aiohttp.

Endpoints:
  POST /ask    - Ask a question
  GET  /health - Health check
  GET  /schema - Get schema summary

Requests must name a loopback host or the bound address, so a DNS-rebinding page can't
reach the server. Browser requests from another site are refused unless their origin is
passed as ``allowed_origins``, which also receive CORS headers. Scripts and curl send no
Origin and work as before. A bearer token is required only when one is configured.
"""

import asyncio
import hmac
import logging
from collections.abc import Iterable

from aiohttp import web

from shared.api.guards import is_allowed_host, normalize_allowed_hosts

from .config import AgentConfig
from .runtime import AgentRuntime, AgentResponse

logger = logging.getLogger(__name__)

# Environment variable that makes the data routes require this bearer token.
AGENT_TOKEN_ENV = "RDST_AGENT_TOKEN"
_AUTHENTICATED_PATHS = {"/ask", "/schema"}


class AgentHTTPServer:
    """
    HTTP API server for a data agent.

    Provides REST endpoints to interact with the agent.
    """

    def __init__(
        self,
        agent_config: AgentConfig,
        token: str | None = None,
        allowed_origins: Iterable[str] = (),
    ):
        """
        Initialize the HTTP server.

        Args:
            agent_config: Agent configuration.
            token: Bearer token clients must send on /ask and /schema; none when not given.
            allowed_origins: Browser origins allowed to call the API, with CORS headers.
        """
        self.config = agent_config
        self.runtime = AgentRuntime(agent_config)
        self.token = token or None
        self.allowed_origins = frozenset(o.rstrip("/") for o in allowed_origins)
        self.allowed_hosts = normalize_allowed_hosts(["127.0.0.1"])
        self.app = web.Application(middlewares=[self._security_middleware])
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Configure HTTP routes."""
        self.app.router.add_post("/ask", self.handle_ask)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/schema", self.handle_schema)
        self.app.router.add_options("/ask", self.handle_options)
        self.app.router.add_options("/health", self.handle_options)
        self.app.router.add_options("/schema", self.handle_options)

    def _allowed_origin(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin", "")
        return bool(origin) and origin.rstrip("/") in self.allowed_origins

    def _cross_site_browser(self, request: web.Request) -> bool:
        """A browser request made by another site's page, rather than a script or curl."""
        if self._allowed_origin(request):
            return False
        site = request.headers.get("Sec-Fetch-Site", "").lower()
        return "Origin" in request.headers or site in ("cross-site", "same-site")

    def _cors_headers(self, request: web.Request) -> dict[str, str]:
        if not self._allowed_origin(request):
            return {}
        origin = request.headers["Origin"]
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Vary": "Origin",
        }

    def _authorized(self, request: web.Request) -> bool:
        if self.token is None:
            return True
        scheme, _, provided = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(provided.strip().encode(), self.token.encode())

    @web.middleware
    async def _security_middleware(self, request: web.Request, handler) -> web.StreamResponse:
        """Check Host, the browser's site, and the optional token; add CORS headers for allowed
        origins."""
        sockname = request.transport.get_extra_info("sockname") if request.transport else None
        server_host = sockname[0] if sockname else None
        if not is_allowed_host(request.headers.get("Host"), self.allowed_hosts, server_host):
            return web.json_response(
                {"success": False, "error": "Host not allowed"}, status=421
            )

        if self._cross_site_browser(request):
            return web.json_response(
                {"success": False,
                 "error": "Cross-site browser requests need rdst agent serve --allow-origin"},
                status=403,
            )

        cors = self._cors_headers(request)
        if request.method != "OPTIONS" and request.path in _AUTHENTICATED_PATHS:
            if not self._authorized(request):
                return web.json_response(
                    {"success": False, "error": "Missing or invalid bearer token"},
                    status=401,
                    headers={"WWW-Authenticate": "Bearer", **cors},
                )

        response = await handler(request)
        response.headers.update(cors)
        return response

    async def handle_options(self, request: web.Request) -> web.Response:
        """Handle CORS preflight requests; the middleware has refused other origins."""
        return web.Response(status=204)

    async def handle_ask(self, request: web.Request) -> web.Response:
        """
        Handle POST /ask requests.

        Request body:
            {"question": "How many users?"}

        Response:
            {"success": true, "sql": "...", "columns": [...], "rows": [...], ...}
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"success": False, "error": "Invalid JSON"},
                status=400,
            )

        question = data.get("question") if isinstance(data, dict) else None
        if not question:
            return web.json_response(
                {"success": False, "error": "Missing 'question' field"},
                status=400,
            )

        # Run query in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, self.runtime.ask, question)

        return web.json_response(response.to_dict())

    async def handle_health(self, request: web.Request) -> web.Response:
        """
        Handle GET /health requests.

        Response:
            {"status": "ok", "agent": "agent-name", "target": "target-name"}
        """
        return web.json_response({
            "status": "ok",
            "agent": self.config.name,
            "target": self.config.target,
        })

    async def handle_schema(self, request: web.Request) -> web.Response:
        """
        Handle GET /schema requests.

        Response:
            {"tables": [...], "source": "semantic_layer|database"}
        """
        loop = asyncio.get_event_loop()
        schema = await loop.run_in_executor(None, self.runtime.get_schema_summary)
        return web.json_response(schema)

    def run(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        """
        Run the HTTP server.

        Args:
            host: Host to bind to (default "127.0.0.1"). The server stays on
                  the loopback interface unless asked otherwise.
            port: Port to listen on (default 8080).
        """
        self.allowed_hosts = normalize_allowed_hosts([host])
        web.run_app(self.app, host=host, port=port, print=self._print_startup)

    def _print_startup(self, message: str) -> None:
        """Custom startup message printer."""
        # Filter out default aiohttp messages
        if "Running on" in message:
            address = message.split("Running on", 1)[1].split()[0]
            print(f"Agent '{self.config.name}' running on {address}")
