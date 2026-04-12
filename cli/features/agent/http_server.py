"""
Agent HTTP Server

Provides an HTTP API for data agents using aiohttp.

Endpoints:
  POST /ask    - Ask a question
  GET  /health - Health check
  GET  /schema - Get schema summary
"""

import asyncio
import logging
from typing import Any

from aiohttp import web

from .config import AgentConfig
from .runtime import AgentRuntime, AgentResponse

logger = logging.getLogger(__name__)


class AgentHTTPServer:
    """
    HTTP API server for a data agent.

    Provides REST endpoints to interact with the agent.
    """

    def __init__(self, agent_config: AgentConfig):
        """
        Initialize the HTTP server.

        Args:
            agent_config: Agent configuration.
        """
        self.config = agent_config
        self.runtime = AgentRuntime(agent_config)
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Configure HTTP routes."""
        self.app.router.add_post("/ask", self.handle_ask)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/schema", self.handle_schema)
        self.app.router.add_options("/ask", self.handle_options)
        self.app.router.add_options("/health", self.handle_options)
        self.app.router.add_options("/schema", self.handle_options)

        # Add CORS middleware
        self.app.middlewares.append(self._cors_middleware)

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler) -> web.Response:
        """Add CORS headers to responses."""
        response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    async def handle_options(self, request: web.Request) -> web.Response:
        """Handle CORS preflight requests."""
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

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

        question = data.get("question")
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

    def run(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """
        Run the HTTP server.

        Args:
            host: Host to bind to (default "0.0.0.0").
            port: Port to listen on (default 8080).
        """
        web.run_app(self.app, host=host, port=port, print=self._print_startup)

    def _print_startup(self, message: str) -> None:
        """Custom startup message printer."""
        # Filter out default aiohttp messages
        if "Running on" in message:
            print(f"Agent '{self.config.name}' running on {message.split('Running on')[1].strip()}")
