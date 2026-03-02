from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


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


def create_app(static_dist_dir: str | None = None) -> FastAPI:
    app = FastAPI(
        title="RDST API",
        version="0.1.0",
        description="API server for RDST web client",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routes import (
        analyze,
        ask,
        configure,
        env,
        interactive,
        status,
        schema,
        registry,
        readyset,
        top,
        init,
        semantic_layer,
        report,
        trial,
    )

    app.include_router(analyze.router, prefix="/api")
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
    app.include_router(trial.router, prefix="/api", tags=["trial"])

    @app.get("/health")
    async def health_check():
        return {"status": "ok"}

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

    return app
