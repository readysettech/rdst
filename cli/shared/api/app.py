from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, TypeAdapter
from starlette.exceptions import HTTPException as StarletteHTTPException


class HealthResponse(BaseModel):
    status: str


# Shared error envelope ------------------------------------------------------
# Every HTTP failure surfaces one {code, message, detail} shape (B7/T24). The
# same shape is emitted on SSE `error` events (see shared.service_events and the
# per-feature SSE serializers) and normalized client-side in lib/sse.ts, so the
# UI never has to parse an ad-hoc failure body or leak a raw ``str(e)``.
_STATUS_CODE_MAP: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "timeout",
}


def _error_envelope(
    code: str, message: str, detail: Any | None = None
) -> dict[str, Any]:
    """Build the shared error envelope. ``detail`` carries the most technical
    fragment (raw string, validation array, or exception type) for an expander;
    ``message`` is always the human-facing summary."""
    return {"code": code, "message": message, "detail": detail}


def register_error_handlers(app: FastAPI) -> None:
    """Attach the shared error-envelope handlers to ``app``.

    Additive by design: HTTP handlers keep ``detail`` populated with the
    original FastAPI value so existing clients that read ``detail`` keep
    working, while ``code`` and ``message`` are added for the shared contract.
    The catch-all handler never echoes ``str(exc)`` as the message.
    """

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_CODE_MAP.get(exc.status_code, "error")
        detail = exc.detail
        if isinstance(detail, dict):
            # A route already emitted a structured body — respect an explicit
            # code/message and pass the rest through as detail.
            code = str(detail.get("code", code))
            message = str(detail.get("message") or detail.get("detail") or "Request failed")
        elif isinstance(detail, str) and detail.strip():
            message = detail
        else:
            message = "Request failed"
        headers = getattr(exc, "headers", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_envelope(code, message, detail),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        first = errors[0].get("msg") if errors else None
        message = str(first) if first else "The request was invalid."
        return JSONResponse(
            status_code=422,
            content=_error_envelope("invalid_request", message, errors),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        # Never surface raw exception text (or even the class name) to the
        # client: several consumers render ``detail`` directly, so it must stay
        # humane. The class name and stack go to the server log for correlation.
        logging.getLogger("rdst.api").exception(
            "Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path
        )
        message = "An unexpected server error occurred."
        return JSONResponse(
            status_code=500,
            content=_error_envelope("internal_error", message, message),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_static_dist_dir(static_dist_dir: str | None) -> Path | None:
    if static_dist_dir:
        dist_dir = Path(static_dist_dir).expanduser().resolve()
    else:
        dist_env = os.environ.get("RDST_WEB_DIST_DIR")
        dist_dir = Path(dist_env).expanduser().resolve() if dist_env else None

        if not _env_flag("RDST_WEB_SERVE_STATIC", default=False):
            return None

    if not dist_dir:
        return None

    index_file = dist_dir / "index.html"
    if not index_file.exists():
        return None

    return dist_dir


def _sse_event_unions() -> dict[str, tuple[Any, list[str]]]:
    """Map SSE event-union schema name → (TypeAdapter-compatible union, route paths).

    Route paths are the OpenAPI paths (including `/api` prefix) that emit the
    union as an SSE stream. They get a `text/event-stream` 200 response added
    to their operation, pointing at the union schema.
    """
    from features.agent.events import ChatEvent
    from features.analyze.events import AnalyzeEvent
    from features.ask.events import AskEvent
    from features.audit.events import AuditEvent, WorkloadEvent
    from features.bootstrap.events import BootstrapEvent
    from features.fleet.events import FleetEvent
    from features.cache.readyset_events import (
        ReadysetCacheEvent,
        ReadysetExplainEvent,
        ReadysetSetupEvent,
    )
    from features.cache.events import CacheRunCompleteEvent
    from features.query_registry.events import QueryBenchmarkEvent
    from features.scan.events import ScanEvent
    from features.top.events import TopEvent
    from features.schema.events import AnnotateEvent
    from shared.run_registry import RunEndEvent
    from shared.service_events import ErrorEvent, ProgressEvent

    BackgroundRunEvent = (
        BootstrapEvent
        | AnnotateEvent
        | ProgressEvent
        | CacheRunCompleteEvent
        | ErrorEvent
        | RunEndEvent
    )

    return {
        "AnalyzeEvent": (AnalyzeEvent, ["/api/analyze", "/api/analyze/quick"]),
        "AskEvent": (AskEvent, ["/api/ask"]),
        "AuditEvent": (AuditEvent, ["/api/audit", "/api/fleet/audit"]),
        "BootstrapEvent": (
            BootstrapEvent,
            ["/api/bootstrap/runs/{run_id}/events"],
        ),
        "BackgroundRunEvent": (
            BackgroundRunEvent,
            ["/api/runs/{run_id}/events"],
        ),
        "ChatEvent": (ChatEvent, ["/api/agents/chat/sessions/{session_id}/message"]),
        "WorkloadEvent": (WorkloadEvent, ["/api/audit/capture"]),
        "FleetEvent": (FleetEvent, ["/api/fleet/status", "/api/fleet/import", "/api/fleet/discover"]),
        "QueryBenchmarkEvent": (
            QueryBenchmarkEvent,
            ["/api/query-registry/benchmark"],
        ),
        "ReadysetCacheEvent": (ReadysetCacheEvent, ["/api/readyset/cache"]),
        "ReadysetExplainEvent": (ReadysetExplainEvent, ["/api/readyset/explain"]),
        "ReadysetSetupEvent": (ReadysetSetupEvent, ["/api/readyset/setup"]),
        "ScanEvent": (ScanEvent, ["/api/scan"]),
        "TopEvent": (TopEvent, ["/api/top/realtime"]),
    }


def _relax_defaulted_required(openapi: dict[str, Any]) -> dict[str, Any]:
    """Remove fields with defaults from `required` in every object schema.

    Pydantic v2 emits default-valued fields as `required` with a `default`
    keyword alongside. openapi-typescript reads `required` and emits the
    field as non-optional TS — forcing every caller to pass optional flags.
    Stripping defaulted fields from `required` yields the optionality the
    Python signature actually implies.
    """

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            required = node.get("required")
            if isinstance(properties, dict) and isinstance(required, list):
                defaulted = {
                    name
                    for name, prop in properties.items()
                    if isinstance(prop, dict) and "default" in prop
                }
                if defaulted:
                    node["required"] = [r for r in required if r not in defaulted]
                    if not node["required"]:
                        node.pop("required")
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(openapi.get("components", {}).get("schemas", {}))
    return openapi


def _inject_sse_schemas(openapi: dict[str, Any]) -> dict[str, Any]:
    """Inject SSE event union schemas into components.schemas and add
    text/event-stream responses to the corresponding SSE routes.
    """
    components = openapi.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    for union_name, (union_type, route_paths) in _sse_event_unions().items():
        adapter = TypeAdapter(union_type)
        union_schema = adapter.json_schema(
            mode="serialization",
            ref_template="#/components/schemas/{model}",
        )
        defs = union_schema.pop("$defs", {})
        for def_name, def_schema in defs.items():
            schemas.setdefault(def_name, def_schema)
        schemas[union_name] = union_schema

        for path in route_paths:
            path_item = openapi.get("paths", {}).get(path)
            if not path_item:
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                operation = path_item.get(method)
                if not operation:
                    continue
                responses = operation.setdefault("responses", {})
                ok = responses.setdefault("200", {"description": "SSE stream"})
                content = ok.setdefault("content", {})
                content["text/event-stream"] = {
                    "schema": {"$ref": f"#/components/schemas/{union_name}"},
                }

    return openapi


def create_app(static_dist_dir: str | None = None) -> FastAPI:
    app = FastAPI(
        title="RDST API",
        version="0.1.0",
        description="API server for RDST web client",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    from features.agent.api import routes as agent
    from features.analyze.api import routes as analyze
    from features.ask.api import routes as ask
    from features.audit.api import routes as audit
    from features.bootstrap.api import routes as bootstrap
    from features.fleet.api import routes as fleet
    from features.guard.api import routes as guard
    from features.cache.api import readyset_routes as readyset
    from features.cache.api import routes as cache
    from features.configure.api import routes as configure
    from features.init.api import routes as init
    from features.interactive.api import routes as interactive
    from features.query_registry.api import routes as registry
    from features.scan.api import routes as scan
    from features.schema.api import routes as schema
    from features.top.api import routes as top
    from features.trial.api import routes as trial
    from features.schema.api import semantic_layer_routes as semantic_layer
    from features.qpdemo.api import routes as qpdemo
    from shared.api.routes import browse, dev, env, report, runs, settings, status

    app.include_router(agent.router, prefix="/api")
    app.include_router(analyze.router, prefix="/api")
    app.include_router(audit.router, prefix="/api", tags=["audit"])
    app.include_router(bootstrap.router, prefix="/api", tags=["bootstrap"])
    app.include_router(browse.router, prefix="/api", tags=["browse"])
    app.include_router(fleet.router, prefix="/api", tags=["fleet"])
    app.include_router(guard.router, prefix="/api")
    app.include_router(cache.router, prefix="/api", tags=["cache"])
    app.include_router(ask.router, prefix="/api", tags=["ask"])
    app.include_router(configure.router, prefix="/api", tags=["configure"])
    app.include_router(interactive.router, prefix="/api", tags=["interactive"])
    app.include_router(status.router, prefix="/api")
    app.include_router(env.router, prefix="/api", tags=["env"])
    app.include_router(schema.router, prefix="/api")
    app.include_router(registry.router, prefix="/api")
    app.include_router(readyset.router, prefix="/api")
    app.include_router(top.router, prefix="/api", tags=["top"])
    app.include_router(init.router, prefix="/api", tags=["init"])
    app.include_router(semantic_layer.router, prefix="/api", tags=["semantic-layer"])
    app.include_router(report.router, prefix="/api", tags=["report"])
    app.include_router(runs.router, prefix="/api", tags=["runs"])
    app.include_router(settings.router, prefix="/api", tags=["settings"])
    app.include_router(trial.router, prefix="/api", tags=["trial"])
    app.include_router(scan.router, prefix="/api", tags=["scan"])
    app.include_router(dev.router, prefix="/api", tags=["dev"])
    app.include_router(qpdemo.router, prefix="/api", tags=["demo"])

    @app.get("/health")
    async def health_check() -> HealthResponse:
        return HealthResponse(status="ok")

    dist_dir = _resolve_static_dist_dir(static_dist_dir)
    if dist_dir:
        index_file = dist_dir / "index.html"

        @app.get("/", include_in_schema=False)
        async def serve_index_root():
            return FileResponse(str(index_file))

        @app.get("/{path:path}", include_in_schema=False)
        async def serve_spa(path: str):
            if path.startswith("api"):
                raise HTTPException(status_code=404, detail="Not Found")

            resolved_path = (dist_dir / path).resolve()
            if dist_dir in resolved_path.parents and resolved_path.is_file():
                return FileResponse(str(resolved_path))

            return FileResponse(str(index_file))

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        _inject_sse_schemas(schema)
        _relax_defaulted_required(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app
