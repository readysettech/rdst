"""The action set an ideal advisor produces for the app-sim workload, as semantic records.

EXPECTED holds what should be recommended (or explicitly kept or skipped); MUST_NOT holds
recommendations that are wrong for this workload. `engines` says where a record applies.
`statements` names the dal.Q statements the action is about.
"""

from __future__ import annotations

from typing import Any

BOTH = ("postgresql", "mysql")

EXPECTED: tuple[dict[str, Any], ...] = (
    {"plant": "P1", "type": "add_index", "engines": BOTH, "table": "products",
     "columns": ["slug"], "unique_ok": True, "priority": "high",
     "statements": ["product_by_slug"],
     "why": "The product page looks products up by slug on every view; with no index each "
            "lookup scans the whole read-mostly products table."},
    {"plant": "P2", "type": "add_index", "engines": BOTH, "table": "orders",
     "columns": ["customer_id", "created_at"], "priority": "high",
     "statements": ["orders_count_for_customer", "orders_for_customer"],
     "why": "Two hot order-history reads filter on customer_id; one sorts by created_at. A "
            "single (customer_id, created_at) index serves both, so a separate (customer_id) "
            "index would be redundant."},
    {"plant": "P3", "type": "drop_index", "engines": BOTH, "table": "order_items",
     "index": "order_items_order_idx", "reason": "duplicate",
     "covered_by": "order_items_order_product_idx",
     "why": "(order_id) is a leading prefix of (order_id, product_id), which serves every "
            "lookup the shorter one does."},
    {"plant": "P4", "type": "drop_index", "engines": BOTH, "table": "events",
     "index": "events_kind_idx", "reason": "unused",
     "why": "No query reads events by kind, and events takes an insert on most requests, so "
            "the index is pure write cost."},
    {"plant": "P5", "type": "skip_index", "engines": BOTH, "table": "events",
     "columns": ["session_id"], "statements": ["support_session_events"],
     "why": "The support lookup by session_id runs about once a minute; maintaining another "
            "index on the insert-heavy events table costs more than it saves. Skip or low "
            "priority."},
    {"plant": "P6", "type": "add_index", "engines": BOTH, "table": "sessions",
     "columns": ["token"], "unique_ok": True, "priority": "high",
     "statements": ["session_touch"],
     "why": "Every request runs UPDATE sessions ... WHERE token = ?; its row lookup scans "
            "sessions. On MySQL the scan also locks every row it reads, serializing touches. "
            "last_seen_at stays unindexed, so PostgreSQL keeps HOT updates."},
    {"plant": "P7", "type": "add_index", "engines": BOTH, "table": "carts",
     "columns": ["expires_at"], "acceptable_columns": [["status", "expires_at"]],
     "priority": "medium", "statements": ["cart_items_purge", "carts_purge"],
     "why": "The periodic cart cleanup DELETEs by expires_at, which has no index, so each run "
            "scans carts (twice, counting the cart_items subquery). expires_at is set once at "
            "insert, so the index adds little write cost."},
    {"plant": "P8", "type": "rewrite_query", "engines": BOTH, "table": "customers",
     "statements": ["customer_by_email"], "query_contains": "lower(customers.email)",
     "rewrite": "WHERE customers.email = %(email_1)s with the bind lower-cased in the app",
     "acceptable_alternatives": {"postgresql": "expression index on lower(email)",
                                 "mysql": "functional index on (lower(email))"},
     "why": "lower() on the column defeats the unique email index, so every login scans "
            "customers. Emails are stored lower case."},
    {"plant": "P9", "type": "change_setting", "engines": ("postgresql",), "name": "work_mem",
     "current": "4MB", "direction": "increase", "statements": ["report_customer_breadth"],
     "why": "The customer-breadth report sorts ~370k joined rows for count(DISTINCT) and "
            "writes temp files at the default 4MB work_mem. A per-role or per-session raise "
            "is enough."},
    {"plant": "P9", "type": "change_setting", "engines": ("mysql",), "name": "tmp_table_size",
     "related": ["max_heap_table_size"], "acceptable_also": ["sort_buffer_size"],
     "current": "1M", "direction": "increase",
     "statements": ["report_top_customers", "report_customer_breadth"],
     "why": "The top-customers report's GROUP BY overflows the 1M in-memory temp table limit "
            "and goes to disk; the breadth report also needs sort merge passes."},
    {"plant": "P10", "type": "keep_index", "engines": BOTH, "table": "payments",
     "index": "payments_created_at_idx", "statements": ["batch_settlement_day"],
     "why": "Only the nightly settlement batch uses it, in bursts. Its scan count is low but "
            "non-zero; dropping it would turn each batch statement into a full scan."},
)

MUST_NOT: tuple[dict[str, Any], ...] = (
    {"type": "drop_index", "index": "payments_created_at_idx", "plant": "P10"},
    {"type": "drop_index", "index": "order_items_order_product_idx", "plant": "P3",
     "why": "Keep the wider index; drop the prefix."},
    {"type": "drop_index", "index": "customers_email_key", "why": "Unique constraint."},
    {"type": "add_index", "table": "orders", "columns": ["customer_id"], "plant": "P2",
     "why": "Should be merged into (customer_id, created_at)."},
    {"type": "add_index", "table": "events", "columns": ["session_id"], "plant": "P5",
     "priority": "high"},
    {"type": "add_index", "table": "events", "columns": ["kind"], "plant": "P4"},
)


def for_engine(engine: str) -> list[dict[str, Any]]:
    return [record for record in EXPECTED if engine in record["engines"]]
