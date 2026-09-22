"""Production FastAPI app with test-only Postgres/Jev boundary controls."""

import os

from shared.api.app import create_app
from features.query_registry import assessment as assessment_module
from features.query_registry.assessment_rubric import MODEL, RUBRIC_VERSION


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
