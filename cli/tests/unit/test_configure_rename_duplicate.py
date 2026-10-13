"""Configure API: one database is added once, and a rename carries its history along."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features.configure.api import routes
from shared.config.targets import TargetsConfig
from shared.monitor.monitor_store import MonitorStore

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


async def _request(method: str, path: str, json_body=None):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


def _target(**changes):
    return {
        "engine": "postgresql",
        "host": "db.internal",
        "port": 5432,
        "database": "shop",
        "user": "reader",
        "password_env": "SHOP_PASSWORD",
        **changes,
    }


async def _add(name: str, **changes):
    return await _request("POST", "/api/configure/targets", {"name": name, "target": _target(**changes)})


async def test_adding_the_same_database_twice_names_the_first(tmp_rdst_home):
    first = await _add("shop")
    again = await _add("shop-2", host="DB.internal")

    assert first.json()["success"] is True
    assert again.status_code == 409
    # The same shape as every other 409 here: the sentence the dialog shows.
    assert again.json() == {"detail": "Already added as shop."}
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.list_targets() == ["shop"]


async def test_another_user_or_jump_host_is_another_database(tmp_rdst_home):
    await _add("shop")
    other_user = await _add("shop-writer", user="writer")
    via_jump = await _add("shop-jump", ssh={"host": "jump.example.com"})

    assert other_user.json()["success"] is True
    assert via_jump.json()["success"] is True


async def test_a_taken_name_is_refused_whatever_its_case(tmp_rdst_home):
    await _add("shop")
    taken = await _add("SHOP", database="other")

    assert taken.status_code == 409
    assert taken.json()["detail"] == "A database named SHOP already exists."


async def test_rename_moves_the_connection_default_and_monitoring_history(tmp_rdst_home):
    await _add("shop")
    await _add("billing", database="billing")
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_default("shop")
    cfg.save()
    store = MonitorStore()
    for target in ("shop", "billing"):
        store.set_settings(target, {"enabled": True})
        store.upsert_item(target, "monitoring_issue", "password_missing", 1.0, "Password", {}, 1000)

    renamed = await _request(
        "PUT", "/api/configure/targets/shop", {"name": "storefront", "target": _target()}
    )

    assert renamed.json() == {
        "success": True,
        "message": "Target 'shop' updated successfully",
        "target_name": "storefront",
    }
    cfg.load()
    assert cfg.list_targets() == ["billing", "storefront"]
    assert cfg.get_default() == "storefront"
    assert cfg.get("storefront")["password_env"] == "SHOP_PASSWORD"
    assert store.monitored_targets() == {"billing", "storefront"}
    assert sorted(item["target"] for item in store.inbox()) == ["billing", "storefront"]
    # A cycle queued under the old name finds monitoring off.
    assert store.read("SELECT value FROM monitor_setting WHERE target='shop'") == [{"value": "0"}]


async def test_rename_moves_analyses_their_fixes_and_the_activity_log(tmp_rdst_home):
    from features.monitor import plan_inputs
    from features.monitor.analysis_digest import digest_for_ref, latest_digest
    from shared.agent_activity.store import AgentActivityStore
    from shared.query_registry import (
        AnalysisResultsRegistry,
        QueryRegistry,
        create_analysis_result,
    )

    await _add("shop")
    registry = QueryRegistry()
    registry.load()
    key, _ = registry.add_query("SELECT * FROM order_items WHERE product_id = 661",
                                target="shop", ask_target="shop", analyzed=True)
    registry.save()
    index = {"table": "order_items", "columns": ["product_id"],
             "sql": "CREATE INDEX idx_order_items_product_id ON order_items (product_id)"}
    analysis_id = AnalysisResultsRegistry().store_analysis_result(key, create_analysis_result(
        query_hash=key, target="shop", performance_metrics={},
        llm_analysis={"index_recommendations": [index]}, explain_plan={}, query_metrics={}))
    ref = f"/api/query-registry/{key}/analysis/{analysis_id}"
    store = MonitorStore()
    store.set_settings("shop", {"enabled": True})
    store._write(lambda conn: conn.execute(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,"
        "finished_at,analysis_ref) VALUES ('shop',?,'user','done',1,1000,1001,?)", (key, ref)))
    activity = AgentActivityStore()
    activity.append({"ts": int(time.time()), "target": "shop", "action": "deep_analysis",
                     "subject": key})
    assert activity.targets() == ["shop"]
    # A read under the new name before the move must not stay cached empty.
    assert digest_for_ref("storefront", key, ref, None) is None

    renamed = await _request(
        "PUT", "/api/configure/targets/shop", {"name": "storefront", "target": _target()}
    )

    assert renamed.json()["target_name"] == "storefront"
    assert latest_digest(store, "storefront", key)["analysis_ref"] == ref
    assert latest_digest(store, "shop", key) is None
    assert digest_for_ref("storefront", key, ref, None)["fix_ready"] is True
    # The index plan reads the analysis's index under the new name, so the fix stays open.
    assert [item["table"] for item in plan_inputs._suggestions("storefront", key, ref)] == \
        ["order_items"]
    library = QueryRegistry().library_store
    assert [row["analysis_id"] for row in library.latest_analysis_refs("storefront", [key])] == \
        [analysis_id]
    assert AnalysisResultsRegistry().get_analysis_by_id(key, analysis_id).target == "storefront"
    reloaded = QueryRegistry()
    reloaded.load()
    entry = reloaded.get_query(key)
    assert (entry.last_target, entry.ask_target) == ("storefront", "storefront")
    assert entry.lifecycle_for("storefront") is not None
    assert activity.targets() == ["storefront"]


def test_a_running_analysis_refuses_the_monitor_rename(tmp_rdst_home):
    from shared.monitor.monitor_store import DatabaseBusyError

    store = MonitorStore()
    store.set_settings("shop", {"enabled": True})
    store._write(lambda conn: conn.execute(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at) "
        "VALUES ('shop','q1','user','running',1,1000)"))

    with pytest.raises(DatabaseBusyError, match="shop is running an analysis or a report"):
        store.rename_target("shop", "storefront")

    assert store.read("SELECT DISTINCT target FROM analyze_job") == [{"target": "shop"}]
    assert store.monitored_targets() == {"shop"}


async def test_rename_refuses_a_taken_name_and_a_busy_database(tmp_rdst_home):
    await _add("shop")
    await _add("billing", database="billing")
    store = MonitorStore()
    store._write(lambda conn: conn.execute(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at) "
        "VALUES ('shop','q1','user','running',1,1000)"))

    taken = await _request(
        "PUT", "/api/configure/targets/shop", {"name": "Billing", "target": _target()}
    )
    busy = await _request(
        "PUT", "/api/configure/targets/shop", {"name": "storefront", "target": _target()}
    )
    invalid = await _request(
        "PUT", "/api/configure/targets/shop", {"name": "my shop!", "target": _target()}
    )

    assert taken.status_code == 409
    assert taken.json()["detail"] == "A database named Billing already exists."
    assert busy.status_code == 409
    assert busy.json()["detail"] == (
        "shop is running an analysis or a report. Rename it when that finishes."
    )
    assert invalid.status_code == 422
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.list_targets() == ["billing", "shop"]


async def test_an_update_without_a_name_keeps_it(tmp_rdst_home, monkeypatch):
    await _add("shop")
    changed = []
    monkeypatch.setattr("features.monitor.loop.connection_changed",
                        lambda target, scheduler=None: changed.append(target))

    updated = await _request("PUT", "/api/configure/targets/shop", {"target": _target(port=6432)})

    assert updated.json()["target_name"] == "shop"
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("shop")["port"] == 6432
    # The collector and the monitor loop read with the new port from their next pass.
    assert changed == ["shop"]
