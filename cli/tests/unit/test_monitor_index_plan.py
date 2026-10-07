from features.monitor.index_plan import CONFIDENT_AGE_S, build_plan

DAY = 86400


def _index(table, name, columns, scans=100, **fields):
    return {"schema": "public", "table": table, "index_name": name, "columns": columns,
            "method": "btree", "unique": False, "primary": False, "scans": scans,
            "window_scans": scans, "size_bytes": 8192, "last_scan_at": None, **fields}


def _query(key, share, *suggestions, name=None):
    return {"key": key, "name": name or key, "share": share, "calls_per_s": 5, "kind": "select",
            "suggestions": list(suggestions)}


def _suggest(table, columns, gain_pct=None, impact=None):
    return {"table": table, "columns": columns, "method": "btree", "gain_pct": gain_pct,
            "verified": gain_pct is not None, "impact": impact}


def _plan(**inputs):
    base = {"engine": "postgresql", "now": 100 * DAY, "observed_s": 3600,
            "counter_age_s": 30 * DAY, "existing": [], "tables": {}, "queries": []}
    return build_plan({**base, **inputs})


def test_suggestions_sharing_a_prefix_become_one_index_that_helps_both():
    plan = _plan(queries=[
        _query("q1", 0.30, _suggest("public.orders", ["customer_id"], gain_pct=80)),
        _query("q2", 0.10, _suggest("orders", ["customer_id", "created_at"], gain_pct=60))])
    [add] = plan["add"]
    assert add["columns"] == ["customer_id", "created_at"]
    assert add["merged"] == [["customer_id"]]
    assert [helped["key"] for helped in add["helps"]] == ["q1", "q2"]
    assert add["benefit"] == round(0.30 * 0.8 + 0.10 * 0.6, 4)
    assert add["label"] == "safe" and add["verified"]
    assert add["sql"].startswith("CREATE INDEX CONCURRENTLY idx_orders_customer_id_created_at")


def test_two_indexes_for_the_same_queries_keep_only_the_stronger_one():
    plan = _plan(queries=[_query("q1", 0.13, _suggest("contacts", ["name"], gain_pct=60),
                                 _suggest("contacts", ["tenant_id", "name"], gain_pct=90))])
    [add] = plan["add"]
    assert add["columns"] == ["tenant_id", "name"]


def test_a_gin_and_a_btree_index_for_the_same_query_keep_only_one():
    gin = {**_suggest("contacts", ["name"], gain_pct=60), "method": "gin"}
    plan = _plan(queries=[_query("q1", 0.1, gin, _suggest("contacts", ["tenant_id", "name"],
                                                          gain_pct=60))])
    assert [(add["columns"], add["method"]) for add in plan["add"]] == [
        (["tenant_id", "name"], "btree")]


def test_a_suggestion_an_existing_index_already_covers_is_not_added():
    plan = _plan(existing=[_index("orders", "orders_cust", ["customer_id", "status"])],
                 queries=[_query("q1", 0.3, _suggest("orders", ["customer_id"], gain_pct=50))])
    assert plan["add"] == []
    assert plan["covered"] == [{"table": "orders", "columns": ["customer_id"],
                                "covered_by": "orders_cust", "queries": ["q1"]}]


def test_write_heavy_tables_are_labeled_and_tiny_gains_there_are_skipped():
    tables = {"events": {"writes_per_s": 400, "peak_writes_per_s": 900},
              "sessions": {"writes_per_s": 30, "peak_writes_per_s": 40}}
    plan = _plan(tables=tables, queries=[
        _query("rare", 0.01, _suggest("events", ["kind"], gain_pct=90)),
        _query("hot", 0.20, _suggest("sessions", ["token"], gain_pct=90))])
    assert [(add["table"], add["label"], add["label_reason"]) for add in plan["add"]] == [
        ("sessions", "worth_it", "write_heavy")]
    assert [(add["table"], add["label_reason"]) for add in plan["skipped"]] == [
        ("events", "writes_outweigh")]


def test_indexing_a_column_that_hot_updates_change_is_flagged():
    tables = {"sessions": {"writes_per_s": 5, "hot_ratio": 0.9, "updated_columns": ["last_seen"]}}
    plan = _plan(tables=tables, queries=[_query("q", 0.2, _suggest("sessions", ["last_seen"], 70))])
    [add] = plan["add"]
    assert add["write_cost"]["hot_risk"] and add["label_reason"] == "hot_updates"
    assert _plan(engine="mysql", tables=tables, queries=[
        _query("q", 0.2, _suggest("sessions", ["last_seen"], 70))])["add"][0]["label"] == "safe"


def test_unverified_suggestions_use_the_analysis_estimate_and_tiny_ones_drop_out():
    plan = _plan(queries=[_query("q1", 0.2, _suggest("orders", ["status"], impact="medium")),
                          _query("q2", 0.001, _suggest("payments", ["state"], impact="low"))])
    [add] = plan["add"]
    assert (add["table"], add["benefit"], add["verified"]) == ("orders", 0.06, False)


def test_unused_and_duplicate_indexes_are_dropped_but_constraints_are_kept():
    existing = [_index("events", "events_pkey", ["id"], scans=0, primary=True, unique=True),
                _index("events", "events_email_key", ["email"], scans=0, unique=True),
                _index("events", "events_kind", ["kind"], scans=0, window_scans=0),
                _index("orders", "orders_cust", ["customer_id"], scans=50),
                _index("orders", "orders_cust_created", ["customer_id", "created_at"], scans=90)]
    plan = _plan(existing=existing, tables={"events": {"writes_per_s": 300}})
    drops = {drop["index_name"]: drop for drop in plan["drop"]}
    assert set(drops) == {"events_kind", "orders_cust"}
    assert drops["events_kind"]["reason"] == "unused"
    assert drops["events_kind"]["confidence"] == "high"
    assert drops["orders_cust"]["covered_by"] == "orders_cust_created"
    assert drops["events_kind"]["sql"] == "DROP INDEX CONCURRENTLY public.events_kind;"
    assert {index["index_name"] for index in plan["keep"]} == {
        "events_pkey", "events_email_key", "orders_cust_created"}


def test_unused_on_young_counters_is_low_confidence_and_replicas_add_a_caveat():
    existing = [_index("events", "events_kind", ["kind"], scans=0, window_scans=0)]
    young = _plan(existing=existing, observed_s=600, counter_age_s=CONFIDENT_AGE_S - 1, replicas=True)
    [drop] = young["drop"]
    assert drop["confidence"] == "low" and drop["replica_caveat"]


def test_an_index_last_used_long_ago_is_stale_on_postgres_16():
    now = 100 * DAY
    existing = [_index("orders", "orders_old", ["region"], scans=12, window_scans=0,
                       last_scan_at=now - 45 * DAY)]
    [drop] = _plan(existing=existing, now=now, counter_age_s=60)["drop"]
    assert (drop["reason"], drop["confidence"]) == ("stale", "high")


def test_mysql_statements_use_backticks():
    plan = _plan(engine="mysql",
                 existing=[_index("events", "e_kind", ["kind"], scans=0, window_scans=0)],
                 queries=[_query("q", 0.3, _suggest("orders", ["customer_id"], impact="high"))])
    assert plan["add"][0]["sql"] == "CREATE INDEX `idx_orders_customer_id` ON `orders` (`customer_id`);"
    assert plan["drop"][0]["sql"] == "ALTER TABLE `events` DROP INDEX `e_kind`;"
    assert plan["sql"].count("\n") == 1


def test_the_plan_is_deterministic():
    queries = [_query(f"q{i}", 0.05, _suggest("orders", [f"c{i % 3}"], gain_pct=40)) for i in range(9)]
    assert _plan(queries=queries) == _plan(queries=list(reversed(queries)))


def test_an_index_without_statistics_is_never_dropped():
    existing = [_index("events", "events_kind", ["kind"], scans=None, window_scans=0)]
    plan = _plan(existing=existing)
    assert plan["drop"] == [] and [index["index_name"] for index in plan["keep"]] == ["events_kind"]


def test_the_same_columns_in_another_order_serve_other_queries():
    plan = _plan(queries=[
        _query("report", 0.12, _suggest("orders", ["created_at", "customer_id"], 50)),
        _query("page", 0.05, _suggest("orders", ["customer_id", "created_at"], 60)),
        _query("count", 0.05, _suggest("orders", ["customer_id"], 60))])
    by_columns = {tuple(add["columns"]): add for add in plan["add"]}
    assert set(by_columns) == {("created_at", "customer_id"), ("customer_id", "created_at")}
    assert by_columns[("customer_id", "created_at")]["merged"] == [["customer_id"]]
    assert {helped["key"] for helped in by_columns[("customer_id", "created_at")]["helps"]} == {
        "page", "count"}


def test_expression_indexes_get_valid_sql_on_both_engines():
    queries = [_query("q", 0.3, _suggest("customers", ["lower(email)"], 70))]
    assert _plan(queries=queries)["add"][0]["sql"] == (
        "CREATE INDEX CONCURRENTLY idx_customers_lower_email ON customers ((lower(email)));")
    assert _plan(engine="mysql", queries=queries)["add"][0]["sql"] == (
        "CREATE INDEX `idx_customers_lower_email` ON `customers` ((lower(email)));")


def test_a_wider_index_names_the_one_it_replaces():
    existing = [_index("order_items", "oi_order_product", ["order_id", "product_id"])]
    plan = _plan(existing=existing, queries=[
        _query("q", 0.2, _suggest("order_items", ["order_id", "product_id", "qty"], 40))])
    assert plan["add"][0]["replaces"] == ["oi_order_product"]


def test_an_idle_index_the_queries_filter_on_is_defeated_not_dropped():
    existing = [_index("invoices", "invoices_no_idx", ["invoice_no"], scans=0, window_scans=0),
                _index("invoices", "invoices_old_idx", ["legacy_ref"], scans=0, window_scans=0)]
    plan = _plan(existing=existing, filtered={"invoices": ["invoice_no", "tenant_id"]})
    assert [drop["index_name"] for drop in plan["drop"]] == ["invoices_old_idx"]
    assert [item["index_name"] for item in plan["defeated"]] == ["invoices_no_idx"]


def test_adds_say_how_queries_use_each_column_and_the_plan_can_be_undone():
    by_tenant = {**_suggest("orders", ["tenant_id", "created_at"], gain_pct=80),
                 "roles": {"tenant_id": "equality", "created_at": "sort"}}
    by_day = {**_suggest("orders", ["tenant_id"], gain_pct=50), "roles": {"tenant_id": "range"}}
    existing = [_index("orders", "orders_note_idx", ["note"], scans=0, window_scans=0,
                       definition="CREATE INDEX orders_note_idx ON public.orders USING btree (note)")]
    plan = _plan(queries=[_query("q1", 0.4, by_tenant), _query("q2", 0.1, by_day)],
                 existing=existing, tables={"orders": {"writes_per_s": 50}})
    [add] = plan["add"]
    # The merged index keeps the strongest use of each column.
    assert add["roles"] == ["equality", "sort"]
    assert plan["drop"][0]["restore_sql"] == (
        "CREATE INDEX CONCURRENTLY orders_note_idx ON public.orders USING btree (note);")
    # Rollback restores the drop before removing the add.
    assert plan["rollback_sql"].splitlines() == [
        plan["drop"][0]["restore_sql"], add["undo_sql"]]
    assert add["undo_sql"] == "DROP INDEX CONCURRENTLY public.idx_orders_tenant_id_created_at;"


def test_a_drop_names_the_writes_that_keep_the_index_up_to_date():
    def writer(key, verb, share, set_columns=()):
        return {"key": key, "name": key, "verb": verb, "share": share, "calls_per_s": 10,
                "set_columns": list(set_columns)}

    plan = _plan(existing=[_index("readings", "readings_value_idx", ["value"], scans=0)],
                 writers={"public.readings": [
                     writer("insert", "insert", 0.10), writer("set_value", "update", 0.05, ["value"]),
                     writer("set_note", "update", 0.20, ["note"])]})
    [drop] = plan["drop"]
    assert [writer["key"] for writer in drop["slows"]] == ["insert", "set_value"]


def test_an_index_type_the_analysis_describes_in_words_is_a_plain_btree():
    described = {**_suggest("contacts", ["tenant_id", "updated_at"], gain_pct=60),
                 "method": "btree (expression index)"}
    plan = _plan(queries=[_query("q1", 0.2, described)])
    [add] = plan["add"]
    assert add["method"] == "btree"
    assert "USING" not in add["sql"]



def test_mysql_indexes_with_the_same_name_on_two_tables_are_judged_apart():
    existing = [_index("cold", "ix_id", ["id"], scans=0, window_scans=0),
                _index("hot", "ix_id", ["id"], scans=100)]
    plan = _plan(engine="mysql", existing=existing)
    assert [(drop["table"], drop["index_name"]) for drop in plan["drop"]] == [("cold", "ix_id")]
    assert [(index["table"], index["index_name"]) for index in plan["keep"]] == [("hot", "ix_id")]


def test_a_covering_index_isnt_dropped_for_a_plain_one_on_the_same_key():
    existing = [_index("events", "events_id_payload", ["id"], scans=5, include=["payload"]),
                _index("events", "events_id", ["id"], scans=50)]
    plan = _plan(existing=existing)
    assert "events_id_payload" not in [drop["index_name"] for drop in plan["drop"]]
