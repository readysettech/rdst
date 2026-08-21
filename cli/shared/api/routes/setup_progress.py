"""Setup progress: what the install has actually done so far.

Every step is derived from real state, never from a "user did this" flag,
so someone who connected a database or analyzed a query from the CLI
arrives with those steps already done.
"""

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from typing import Optional

from shared.api.guards import require_local_request
from shared.config.targets import TargetsConfig

router = APIRouter()


class SetupProgressResponse(BaseModel):
    """The five setup signals for one target, all independent."""

    target: str = ""
    connected: bool = False
    schema_built: bool = False
    queries_found: bool = False
    analyzed: bool = False
    compared: bool = False
    error: Optional[str] = None


@router.get("/setup-progress")
async def get_setup_progress(
    request: Request,
    target: Optional[str] = Query(
        None, description="Target to report on; defaults to the default target"
    ),
) -> SetupProgressResponse:
    """Report how far setup has got for one target.

    The three library-derived signals share a single connection, so the
    whole answer costs one config read, one file check and one short
    SQLite read.
    """
    require_local_request(request)

    try:
        cfg = TargetsConfig()
        cfg.load()

        # Readyset deployments are implementation targets rather than
        # databases a user connects, matching what /status exposes.
        names = [
            name
            for name in cfg.list_targets()
            if (cfg.get(name) or {}).get("target_type") != "readyset"
        ]
        active = target or cfg.get_default() or ""
        if not active:
            return SetupProgressResponse(connected=bool(names))

        from features.schema.semantic_layer import SemanticLayerManager
        from shared.query_registry import QueryRegistry

        store = QueryRegistry().library_store
        signals = store.setup_signals(active) if store is not None else {}
        return SetupProgressResponse(
            target=active,
            connected=bool(names),
            schema_built=SemanticLayerManager().exists(active),
            queries_found=bool(signals.get("queries_found")),
            analyzed=bool(signals.get("analyzed")),
            compared=bool(signals.get("compared")),
        )

    except Exception as e:
        return SetupProgressResponse(error=str(e))
