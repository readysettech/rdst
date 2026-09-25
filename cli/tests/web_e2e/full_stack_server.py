"""Production FastAPI app with test-only Postgres and optional Jev controls."""

import os

from shared.api.app import create_app
from features.query_registry import assessment as assessment_module
from features.query_registry.assessment_rubric import MODEL, RUBRIC_VERSION

if os.environ.get("RDST_E2E_FAST_MONITOR") == "1":
    from features.query_registry import scheduler
    from features.monitor import analyze_queue, report

    scheduler.ACTIVE_INTERVAL_SECONDS = 3.0
    scheduler.MAX_DUTY_CYCLE = 1.0
    # Monitored retries follow ACTIVE_INTERVAL_SECONDS on this compressed clock.
    scheduler.DEGRADED_INTERVAL_SECONDS = 10.0
    queue_init = analyze_queue.AnalyzeQueue.__init__

    def fast_queue_init(self, *args, **kwargs):
        kwargs.setdefault("interval_s", 2)
        queue_init(self, *args, **kwargs)

    analyze_queue.AnalyzeQueue.__init__ = fast_queue_init
    # Classify monitored queries at the pace of an open Database page.
    assessment_module.BACKGROUND_CONCURRENCY = assessment_module.CONCURRENCY
    # A first nightly report can preempt the user-requested report on a fresh
    # target. The separate E2E finding tracks that product behavior.
    report.ReportScheduler.tick = lambda self: None


class _ControlledJevClient:
    def assess(self, **kwargs):
        score = 3.2 if "ORDER BY" in str(kwargs.get("sql") or "").upper() else 1.2
        choice = "possible_concern" if score >= 2 else "no_evidence"
        probabilities = {
            "no_evidence": 1.0 if choice == "no_evidence" else 0.0,
            "possible_concern": 1.0 if choice == "possible_concern" else 0.0,
            "strong_concern": 0.0,
            "insufficient_context": 0.0,
        }
        answer = {"type": "choice", "choice": choice, "probabilities": probabilities, "confidence": 0.8}
        return {
            "model": MODEL, "rubric_version": RUBRIC_VERSION,
            "answers": {
                "access_expression_risk": answer, "index_coverage": answer,
                "join_growth": answer, "repeated_work": answer, "broad_work": answer,
                "priority": {"type": "score", "score": score,
                             "legend": {str(i): str(i) for i in range(5)},
                             "probabilities": {str(i): 0.2 for i in range(5)},
                             "confidence": 0.75},
            },
            "usage": {"input_tokens": 500, "output_tokens": 20},
        }


if os.environ.get("RDST_E2E_CONTROLLED_JEV") == "1":
    assessment_module.query_assessment_worker._client = _ControlledJevClient()
    assessment_module.is_signed_in_locally = lambda: True


app = create_app(static_dist_dir=os.environ.get("RDST_WEB_DIST_DIR"))

if os.environ.get("RDST_CIM_ENGINE"):
    from tests.web_e2e.ci_shop.routes import router as ci_shop_router

    app.include_router(ci_shop_router)

    import asyncio
    from contextlib import asynccontextmanager
    from tests.web_e2e.ci_shop.routes import _teardown

    app_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def ci_shop_lifespan(app):
        async with app_lifespan(app):
            try:
                yield
            finally:
                await asyncio.to_thread(_teardown)

    app.router.lifespan_context = ci_shop_lifespan



@app.post("/__test/execute/{target}")
async def execute_workload(target: str, body: dict):
    import psycopg2
    from shared.api.target_guard import resolve_target_config
    from shared.db_connection import postgres_connection_kwargs, resolve_connection_params

    _, config = resolve_target_config(target)
    params = resolve_connection_params(target=target, target_config=config)
    connection = psycopg2.connect(**postgres_connection_kwargs(params))
    try:
        with connection.cursor() as cursor:
            for _ in range(max(1, min(int(body.get("repeat") or 1), 20))):
                cursor.execute(str(body["sql"]))
                if cursor.description:
                    cursor.fetchall()
        connection.commit()
    finally:
        connection.close()
    return {"success": True}


@app.post("/__test/discovery/{target}")
async def trigger_discovery(target: str):
    from features.query_registry.discovery import query_discovery

    event = await query_discovery.collector_for(target).collect_now()
    return {"event": event.event, "data": event.data}
