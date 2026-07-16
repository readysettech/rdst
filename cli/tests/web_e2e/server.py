"""FastAPI test server for deterministic RDST browser workflows.

The browser always talks to the production FastAPI routes.  This module only
replaces service symbols with the deterministic subclasses in fakes.py, which
override the methods that would otherwise contact a database, Readyset, or an
LLM.  Everything else, including the audit history and semantic layer read
paths, runs the production code against real on-disk state.
"""

from __future__ import annotations

import os

from tests.web_e2e.fakes import (
    FakeAnalyzeService,
    FakeAskService,
    FakeAuditService,
    FakeCacheService,
    FakeInitService,
    FakeScanService,
    FakeSchemaService,
    FakeTopService,
    fake_autocomplete_schema,
)


# Patch the service symbols looked up by production route functions.  The
# routes themselves, their dependencies, and their serializers stay intact.
from features.analyze.api import routes as analyze_routes
from features.ask.api import routes as ask_routes
from features.audit.api import routes as audit_routes
from features.cache.api import routes as cache_routes
from features.init.api import routes as init_routes
from features.scan.api import routes as scan_routes
from features.schema.api import routes as schema_routes
from features.schema.api import semantic_layer_routes
from features.top.api import routes as top_routes


analyze_routes.AnalyzeService = FakeAnalyzeService
ask_routes.AskService = FakeAskService
audit_routes.AuditService = FakeAuditService
cache_routes.CacheService = FakeCacheService
init_routes.InitService = FakeInitService
scan_routes.ScanService = FakeScanService
semantic_layer_routes.SchemaService = FakeSchemaService
top_routes.TopService = FakeTopService
schema_routes.collect_all_tables_schema = fake_autocomplete_schema


from shared.api.app import create_app


app = create_app(static_dist_dir=os.environ.get("RDST_WEB_DIST_DIR"))
