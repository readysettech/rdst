"""/api/demo/facts: what the demo builds, from the seed and workload metadata."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.qpdemo import service
from features.qpdemo.api import routes
from features.qpdemo.workload import WORKLOAD


def test_facts_come_from_the_seed_header_the_dockerfile_and_the_workload(tmp_path):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app, client=("127.0.0.1", 54321))
    facts = client.get("/api/demo/facts").json()
    assert facts["rows"] == 1_500_000
    assert facts["tables"] == {"customers": 100_000, "products": 5_000, "orders": 1_500_000,
                               "order_items": 4_000_000}
    assert facts["pg_version"] == "16"
    assert facts["query_count"] == len(WORKLOAD) == 44
    assert facts["download_mb"] == service.IMAGE_DOWNLOAD_MB
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM public.ecr.aws/docker/library/postgres:17\n")
    assert service.pg_version(dockerfile) == "17"
    assert service.pg_version(Path(tmp_path / "missing"), "repo/img:pg15-v2") == "15"
    assert service.pg_version(Path(tmp_path / "missing"), "repo/img:v1") == ""
    assert service.seed_scale(tmp_path / "missing.sql") == {}
