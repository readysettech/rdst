"""Durable, active-target-only Jev quick-assessment worker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import random
import time
import uuid
from typing import Any

from features.schema.assessment_context import (
    collect_assessment_context,
    target_identity,
)
from shared.account_session import is_signed_in_locally
from shared.config.targets import TargetsConfig
from shared.llm_manager.jev_client import JevClient, JevClientError
from shared.query_registry import QueryRegistry

from .assessment_rubric import MODEL, RUBRIC_VERSION, finding_mask, validate_response

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.5
IDLE_SECONDS = 15.0
CONCURRENCY = 8
LEASE_SECONDS = 120.0


class AssessmentWorker:
    """One in-process worker with CONCURRENCY request lanes, admitted only by
    selected-target streams. Each lane claims and assesses one identity per
    Jev call; lanes refill as calls return.

    Browser target state is synchronized through localStorage. If overlapping
    tabs briefly subscribe to different targets, the most recently opened
    subscription owns assessment admission; when it closes, ownership returns
    to the most recent remaining subscription.
    """

    def __init__(self) -> None:
        self._owner = f"{os.getpid()}:{uuid.uuid4()}"
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._subscriptions: dict[str, tuple[int, int]] = {}
        self._sequence = 0
        self._active_target: str | None = None
        self._stopping = False
        self._processed = 0
        self._client = JevClient()
        # Lanes share one registry file; only the vendor call and schema
        # collection run in parallel, store access is serialized.
        self._store_lock = asyncio.Lock()

    @property
    def active_target(self) -> str | None:
        return self._active_target

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        registry = QueryRegistry()
        store = registry.library_store
        if store is not None and store.path.exists():
            await self._store(store.recover_expired_assessment_claims)
        self._task = asyncio.create_task(self._run(), name="query-assessment")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._subscriptions.clear()
        self._active_target = None

    def select_target(self, target: str) -> None:
        self._sequence += 1
        count, _ = self._subscriptions.get(target, (0, 0))
        self._subscriptions[target] = (count + 1, self._sequence)
        self._active_target = target
        self._wake.set()

    def release_target(self, target: str) -> None:
        current = self._subscriptions.get(target)
        if current is None:
            return
        count, order = current
        if count <= 1:
            self._subscriptions.pop(target, None)
        else:
            self._subscriptions[target] = (count - 1, order)
        self._active_target = (
            max(self._subscriptions, key=lambda item: self._subscriptions[item][1])
            if self._subscriptions
            else None
        )
        self._wake.set()

    def _current_config(self, target: str) -> dict[str, Any] | None:
        config = TargetsConfig()
        config.load()
        value = config.get(target)
        return dict(value) if isinstance(value, dict) else None

    def _still_active(self, target: str, identity: str) -> bool:
        if self._active_target != target:
            return False
        config = self._current_config(target)
        return bool(config and target_identity(config) == identity)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def _run(self) -> None:
        lanes: set[asyncio.Task[bool]] = set()
        try:
            while not self._stopping:
                target = self._active_target
                if not target:
                    if lanes:
                        await asyncio.wait(lanes)
                        lanes.clear()
                    await self._wait(IDLE_SECONDS)
                    continue
                while len(lanes) < CONCURRENCY:
                    lanes.add(asyncio.create_task(self._lane(target)))
                done, lanes = await asyncio.wait(lanes, return_when=asyncio.FIRST_COMPLETED)
                did_work = any(task.result() for task in done)
                if not did_work and not lanes:
                    await self._wait(IDLE_SECONDS)
                elif not did_work:
                    await self._wait(POLL_SECONDS)
        finally:
            for task in lanes:
                task.cancel()
            if lanes:
                await asyncio.gather(*lanes, return_exceptions=True)

    async def _store(self, fn, *args, **kwargs):
        async with self._store_lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def _lane(self, target: str) -> bool:
        try:
            return await self._process_one(target)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Quick assessment worker cycle failed", exc_info=True)
            return False

    async def _process_one(self, target: str) -> bool:
        config = self._current_config(target)
        if config is None or self._active_target != target:
            return False
        identity = target_identity(config)
        registry = QueryRegistry()
        store = registry.library_store
        if store is None:
            return False
        await self._store(store.prepare_assessments, target, identity)
        if not is_signed_in_locally():
            await self._store(store.set_target_assessment_state,
                target,
                identity,
                "waiting_auth",
                error_code="login_required",
            )
            self._publish(target)
            return False

        request_id = str(uuid.uuid4())
        prefer_new = self._processed % 5 != 4
        claim = await self._store(store.claim_assessment,
            target,
            identity,
            owner=self._owner,
            request_id=request_id,
            lease_seconds=LEASE_SECONDS,
            prefer_new=prefer_new,
        )
        if claim is None:
            return False
        self._processed += 1
        try:
            context = await asyncio.to_thread(
                collect_assessment_context,
                claim["sql"],
                target=target,
                target_config=config,
                store=store,
            )
        except ValueError as exc:
            await self._store(store.retry_assessment,
                claim["id"],
                claim["generation"],
                self._owner,
                error_code=str(exc),
                next_attempt_at=0,
                status="unsupported",
            )
            self._publish(target)
            return True
        except Exception:
            await self._store(store.retry_assessment,
                claim["id"],
                claim["generation"],
                self._owner,
                error_code="schema_unavailable",
                next_attempt_at=time.time() + 30,
                status="waiting_connection",
            )
            await self._store(store.set_target_assessment_state,
                target,
                identity,
                "waiting_connection",
                error_code="schema_unavailable",
            )
            self._publish(target)
            return False

        # Selection/config may have changed while metadata I/O was in flight.
        # The claim remains safely retryable and no provider request is sent.
        if not self._still_active(target, identity):
            await self._store(store.retry_assessment,
                claim["id"],
                claim["generation"],
                self._owner,
                error_code="selection_changed",
                next_attempt_at=time.time(),
            )
            return False

        state = {
            "normalized_sql": claim["sql"],
            "dialect": config.get("engine") or "unknown",
            "schema_evidence": context.payload,
        }
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
        input_fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        try:
            response = await asyncio.to_thread(
                self._client.assess,
                request_id=claim["request_id"],
                rubric_version=RUBRIC_VERSION,
                sql=claim["sql"],
                dialect=str(config.get("engine") or "unknown"),
                evidence=context.payload,
                input_fingerprint=input_fingerprint,
            )
            normalized = validate_response(response)
        except JevClientError as exc:
            if exc.status in {401, 403}:
                status, delay = "waiting_auth", 60.0
            elif exc.status in {413, 422}:
                status, delay = "unsupported", 0.0
            elif exc.code in {"JEV_RESULT_UNKNOWN", "JEV_INVALID_RESPONSE"}:
                status, delay = "paused", 0.0
            elif exc.code == "JEV_NOT_CONFIGURED":
                status, delay = "retry_wait", 60.0
            else:
                status = "retry_wait"
                delay = exc.retry_after or min(60.0, 2 ** min(claim["generation"], 6))
                delay *= random.uniform(0.8, 1.2)
            await self._store(store.retry_assessment,
                claim["id"],
                claim["generation"],
                self._owner,
                error_code=exc.code.lower(),
                next_attempt_at=time.time() + delay,
                status=status,
            )
            self._publish(target)
            return True
        except ValueError:
            await self._store(store.retry_assessment,
                claim["id"],
                claim["generation"],
                self._owner,
                error_code="invalid_response",
                next_attempt_at=time.time() + 30,
            )
            self._publish(target)
            return True
        except asyncio.CancelledError:
            await self._store(store.retry_assessment,
                claim["id"],
                claim["generation"],
                self._owner,
                error_code="shutdown",
                next_attempt_at=time.time(),
            )
            raise

        assessed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        saved = await self._store(store.complete_assessment,
            claim["id"],
            claim["generation"],
            self._owner,
            result=normalized,
            priority_score=normalized["priority_score"],
            band=normalized["band"],
            confidence=normalized["confidence"],
            finding_mask=finding_mask(normalized["findings"]),
            model=MODEL,
            rubric_version=RUBRIC_VERSION,
            input_fingerprint=input_fingerprint,
            schema_fingerprint=context.fingerprint,
            schema_collected_at=context.collected_at,
            schema_coverage=f"{context.coverage} ({context.summary()})",
            assessed_at=assessed_at,
        )
        if saved and self._active_target == target:
            self._publish(target)
        return True

    @staticmethod
    def _publish(target: str) -> None:
        try:
            from .discovery import query_discovery

            query_discovery.publish_assessment_update(target)
        except Exception:
            logger.debug("Could not publish quick assessment update", exc_info=True)


query_assessment_worker = AssessmentWorker()
