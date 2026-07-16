"""Unmodified production FastAPI app used by the Postgres browser smoke test."""

import os

from shared.api.app import create_app


app = create_app(static_dist_dir=os.environ.get("RDST_WEB_DIST_DIR"))
