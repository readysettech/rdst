"""In-process integration tests for the top API endpoints.

The happy paths (`pg_stat_statements` snapshot, realtime activity
streaming) require a live database to produce meaningful events and
are covered in `test_realdb_top_api.py`. This file pins the guard
behavior — bad target / missing password should never reach the
service.
"""

from __future__ import annotations


async def test_top_returns_404_for_unknown_target(client, tmp_rdst_home):
    """Unknown target → guard 404s before TopService runs."""
    response = await client.get("/api/top?target=does-not-exist")
    assert response.status_code == 404, response.text
