"""Deterministic subclasses of the production services for browser tests.

Each fake extends its real service and overrides only the methods that would
contact a database, Readyset, an LLM, or a remote host. Overridden methods
keep the exact production signatures (test_web_e2e_service_fakes.py enforces
this), unfaked methods run the real code, and fixture payloads are validated
into the same typed events the real services yield. Where a real read path
depends on state a faked method would have written (audit history, the
semantic layer), the fake persists that state through the production storage
so the read paths stay real.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import TypeAdapter

from tests.web_e2e.fixture_store import FixtureStore


fixtures = FixtureStore()


from features.analyze.events import AnalyzeEvent
from features.analyze.service import AnalyzeService
from features.ask.events import AskEvent
from features.ask.service import AskService
from features.audit.events import AuditEvent
from features.audit.service import AuditService
from features.bootstrap.events import BootstrapEvent
from features.bootstrap.service import TargetBootstrapService
from features.cache.events import CacheEvent
from features.cache.service import CacheService
from features.init.events import InitEvent
from features.init.service import InitService
from features.scan.events import ScanEvent
from features.scan.service import ScanService
from features.schema.events import SchemaEvent
from features.schema.models import SchemaStatus
from features.schema.semantic_models import SemanticLayer
from features.schema.service import SchemaService
from features.top.events import TopEvent
from features.top.service import TopService


ANALYZE_EVENT = TypeAdapter(AnalyzeEvent)
ASK_EVENT = TypeAdapter(AskEvent)
AUDIT_EVENT = TypeAdapter(AuditEvent)
BOOTSTRAP_EVENT = TypeAdapter(BootstrapEvent)
CACHE_EVENT = TypeAdapter(CacheEvent)
INIT_EVENT = TypeAdapter(InitEvent)
SCAN_EVENT = TypeAdapter(ScanEvent)
SCHEMA_EVENT = TypeAdapter(SchemaEvent)
SCHEMA_STATUS = TypeAdapter(SchemaStatus)
TOP_EVENT = TypeAdapter(TopEvent)


class FakeAnalyzeService(AnalyzeService):
    async def analyze(self, input, options):
        del input, options
        async for event in fixtures.events("analyze", ANALYZE_EVENT):
            yield event


class FakeAskService(AskService):
    async def ask(self, input, options):
        del input, options
        async for event in fixtures.events("ask", ASK_EVENT):
            yield event

    async def resume(self, session_id, clarification_answers=None):
        del session_id, clarification_answers
        async for event in fixtures.events("ask_resume", ASK_EVENT):
            yield event


class FakeAuditService(AuditService):
    async def audit_single(self, target_name, *, insights=False, save=True):
        del target_name, insights, save
        snapshot_id: str | None = None
        async for event in fixtures.events("audit", AUDIT_EVENT):
            if event.type == "snapshot_saved":
                snapshot_id = event.snapshot_id
            if event.type == "target_complete" and snapshot_id is not None:
                # Persist through the real store so the history routes,
                # which read it from disk, serve this run for real.
                from features.fleet import SnapshotStore

                await asyncio.to_thread(
                    SnapshotStore().save_raw, snapshot_id, event.result
                )
            yield event


class FakeTargetBootstrapService(TargetBootstrapService):
    async def run(
        self,
        target,
        target_config,
        options=None,
        key_wakeup=None,
    ):
        response = fixtures.take("bootstrap", default=None)
        if response is None:
            async for event in super().run(
                target,
                target_config,
                options,
                key_wakeup=key_wakeup,
            ):
                yield event
            return

        async for event in fixtures.replay(response, BOOTSTRAP_EVENT):
            yield event
            if event.type == "needs_key":
                if key_wakeup is None:
                    raise RuntimeError("Bootstrap fixture needs a resume event")
                await key_wakeup.wait()
                key_wakeup.clear()


class FakeCacheService(CacheService):
    async def get_status(self, input_data):
        del input_data
        async for event in fixtures.events("cache_status", CACHE_EVENT):
            yield event

    async def list_caches(self, input_data):
        del input_data
        async for event in fixtures.events("cache_list", CACHE_EVENT):
            yield event

    async def deploy(self, input_data, options):
        del input_data, options
        async for event in fixtures.events("cache_deploy", CACHE_EVENT):
            yield event

    async def add_cache(self, input_data, options):
        del input_data, options
        async for event in fixtures.events("cache_add", CACHE_EVENT):
            yield event

    async def register_cache_endpoint(self, input_data, host, port):
        del input_data, host, port
        async for event in fixtures.events("cache_register", CACHE_EVENT):
            yield event

    async def lifecycle(self, input_data, operation):
        del input_data, operation
        async for event in fixtures.events("cache_lifecycle", CACHE_EVENT):
            yield event

    async def run_comparison(self, input_data, iterations=5, warmup=2):
        del input_data, iterations, warmup
        async for event in fixtures.events("cache_run", CACHE_EVENT):
            yield event

    async def delete_cache(self, input_data):
        del input_data
        async for event in fixtures.events("cache_delete", CACHE_EVENT):
            yield event


class FakeScanService(ScanService):
    async def scan_directory(self, input_data, options):
        del input_data, options
        async for event in fixtures.events("scan", SCAN_EVENT):
            yield event


class FakeSchemaService(SchemaService):
    """Fakes only the database-touching operations.

    get_schema and delete are inherited real; init_events persists the
    fixture's layer through the real manager so those read paths, and the
    real get_status, serve real on-disk state. get_status stays overridable
    by a fixture so specs can inject status failures.
    """

    def get_status(self, target):
        response = fixtures.take("semantic_status", default=None)
        if response is None:
            return super().get_status(target)
        if "value" not in response:
            raise RuntimeError("Fixture 'semantic_status' must contain a value")
        return SCHEMA_STATUS.validate_python(response["value"])

    async def init_events(self, target, target_config, options=None):
        del target_config, options
        response = fixtures.take("semantic_init")
        layer_data = response.get("layer")
        if layer_data is not None:
            self._manager.save(SemanticLayer.from_dict({**layer_data, "target": target}))
        async for event in fixtures.replay(response, SCHEMA_EVENT):
            yield event

    def refresh(self, target, target_config):
        del target, target_config
        return fixtures.value("semantic_refresh")


class FakeTopService(TopService):
    async def get_top_queries(self, input, options):
        del input, options
        async for event in fixtures.events("top_historical", TOP_EVENT):
            yield event

    async def stream_realtime(self, input, options, duration=None):
        del input, options, duration
        async for event in fixtures.events("top_realtime", TOP_EVENT):
            yield event


class FakeInitService(InitService):
    async def validate_all_events(self, target_names=None):
        del target_names
        async for event in fixtures.events("init_validate", INIT_EVENT):
            yield event


SERVICE_FAKES = [
    FakeAnalyzeService,
    FakeAskService,
    FakeAuditService,
    FakeTargetBootstrapService,
    FakeCacheService,
    FakeInitService,
    FakeScanService,
    FakeSchemaService,
    FakeTopService,
]


def fake_autocomplete_schema(target_config: dict[str, Any]) -> dict[str, Any]:
    del target_config
    return {
        "success": True,
        "tables": fixtures.value("autocomplete_schema", default={}),
    }
